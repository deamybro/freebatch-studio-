"""Security helpers: filename sanitization, SSRF guards, secret masking, CSRF.

FreeBatch Studio is a local single-user app, but we still apply standard
defenses: no directory traversal, no private-network SSRF targets, no
execution of uploaded content, and no leaking of API keys.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import socket
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import urlparse

_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_SLUG_RE = re.compile(r"[^a-zA-Z0-9_\-]+")
_PRIVATE_HOST_RE = re.compile(
    r"^(localhost|.*\.localhost|.*\.local|.*\.internal|0\.0\.0\.0)$",
    re.IGNORECASE,
)
_DEFAULT_MAX_NAME = 120


def json_safe(obj):
    """Recursively make a structure JSON-safe.

    Some provider ``capabilities()`` dicts use tuple keys (e.g.
    ``AGNES_IMAGE_DIMENSIONS`` is keyed by ``(ratio, tier)``). JSON cannot have
    non-string dict keys, and FastAPI's encoder coerces tuple keys into lists
    (which then fail as dict keys). Convert tuple/non-string keys to readable
    strings before serialization.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            new_key = " ".join(str(part) for part in key) if isinstance(key, tuple) else str(key)
            out[new_key] = json_safe(value)
        return out
    if isinstance(obj, (list, tuple)):
        return [json_safe(item) for item in obj]
    return obj


class SecurityError(Exception):
    """Raised when a security check fails (SSRF, CSRF, unsafe filename)."""


def sanitize_filename(name: str, max_len: int = _DEFAULT_MAX_NAME) -> str:
    """Return a safe single-path-segment filename (no traversal, no reserved names).

    ``max_len`` caps the returned length. Keeps a reasonable extension.
    """
    name = (name or "").replace("\\", "/")
    base = PurePosixPath(name).name
    if not base:
        base = "output"
    base = _ILLEGAL_FILENAME_CHARS.sub("_", base)
    stem, dot, ext = base.rpartition(".")
    candidate = base
    if dot and stem:
        candidate = stem[: max(0, max_len - len(ext) - 1)] + dot + ext
    candidate = candidate.rstrip(" .")
    candidate = candidate or "output"
    if len(candidate) > max_len:
        candidate = candidate[:max_len].rstrip(" .")
    if not candidate:
        candidate = "output"
    root = candidate.split(".")[0].upper()
    if root in _WINDOWS_RESERVED:
        candidate = "_" + candidate
    return candidate


def safe_slug(text: str, max_len: int = 40) -> str:
    """Turn arbitrary prompt text into a safe filename slug."""
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("_", text).strip("_")
    slug = slug or "output"
    return slug[:max_len].rstrip("_")


def safe_basename_for_job(job_index: int, prompt: str, extension: str) -> str:
    """Build an output filename like ``0001_red_circle.png`` for a job."""
    ext = sanitize_filename(extension, 16).split(".")[-1].lower() or "bin"
    return f"{int(job_index):04d}_{safe_slug(prompt)}.{ext}"


def mask_secret(secret: str | None) -> str:
    """Return a masked display form like ``agn_****7F``. Never the full secret."""
    if not secret:
        return "Not configured"
    value = str(secret)
    if len(value) <= 8:
        return value[:2] + "****" + value[-2:]
    return value[:4] + "****" + value[-4:]


def _is_private_host(host: str) -> bool:
    host = (host or "").strip().strip("[]")
    lowered = host.lower()
    if _PRIVATE_HOST_RE.match(lowered):
        return True
    try:
        addr = ipaddress.ip_address(lowered)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        )
    except ValueError:
        return False


def validate_remote_url(
    url: str,
    allow_private: bool = False,
    require_https: bool = True,
) -> str:
    """Validate that ``url`` is a plausible public HTTP(S) URL (SSRF guard).

    Returns the normalized URL string or raises :class:`SecurityError`.
    HTTPS is required by default (``require_https=False`` opts into plain
    http). Resolves hostnames and rejects any resolution that lands on a
    private, loopback or link-local address unless ``allow_private`` is set.
    """
    try:
        parsed = urlparse((url or "").strip())
    except ValueError as exc:
        raise SecurityError("malformed URL") from exc
    if parsed.scheme not in ("https", "http"):
        raise SecurityError("only http(s) URLs are allowed")
    if require_https and parsed.scheme != "https":
        raise SecurityError("only https URLs are allowed (plain http rejected)")
    if not parsed.netloc or not parsed.hostname:
        raise SecurityError("URL is missing a host")
    hostname = parsed.hostname.rstrip(".").lower()
    if not allow_private and _is_private_host(hostname):
        raise SecurityError("private / local network hosts are not allowed")
    if not allow_private and hostname in {"localhost", "localhost.localdomain"}:
        raise SecurityError("localhost is not an allowed remote target")
    if not allow_private:
        try:
            addrinfo = socket.getaddrinfo(hostname, None)
        except OSError as exc:
            raise SecurityError(f"could not resolve remote host: {hostname}") from exc
        for entry in addrinfo:
            try:
                addr = ipaddress.ip_address(entry[4][0])
            except ValueError:
                continue
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                raise SecurityError(
                    "remote URL resolves to a private/loopback address"
                )
    return (url or "").strip()


def zip_arcname_is_safe(arcname: str) -> bool:
    """Return True when an archive member name cannot escape the archive."""
    arcname = (arcname or "").replace("\\", "/")
    if not arcname or arcname.startswith("/"):
        return False
    if re.match(r"^[a-zA-Z]:", arcname):
        return False
    parts = arcname.split("/")
    return not any(p in ("", ".", "..") for p in parts)


class CsrfProtector:
    """Constant-time CSRF token storage and validation (single-user app).

    The secret lives in the settings table so it survives restarts. Tokens are
    compared in constant time and every mutating API call must present them.
    """

    _KEY = "csrf_secret"

    def __init__(self, db) -> None:
        self._db = db
        self._token: str | None = None

    def check_env_setup(self) -> None:
        """Prepare the CSRF secret (called at startup)."""
        self.token()

    def token(self) -> str:
        """Return the current CSRF token, generating and persisting it on first use."""
        if self._token:
            return self._token
        stored = self._db.get_setting(self._KEY)
        if stored:
            self._token = stored
            return stored
        fresh = secrets.token_urlsafe(32)
        self._db.set_setting(self._KEY, fresh)
        self._token = fresh
        return fresh

    def validate(self, token: str | None) -> None:
        """Raise :class:`SecurityError` when ``token`` is missing or invalid."""
        expected = self.token()
        if not token or not secrets.compare_digest(expected, token):
            raise SecurityError("invalid or missing CSRF token")
