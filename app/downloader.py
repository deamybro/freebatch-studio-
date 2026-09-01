"""Streaming downloads with .part files, size/MIME validation and safe rename."""

from __future__ import annotations

import base64
import binascii
import logging
import re
from pathlib import Path

import httpx

from app.security import SecurityError, sanitize_filename, validate_remote_url

logger = logging.getLogger("freebatch")

# (magic bytes, allowed mime families)
_MAGIC = [
    (b"\xff\xd8\xff", {"image/jpeg", "image/jpg"}),
    (b"\x89PNG\r\n\x1a\n", {"image/png"}),
    (b"GIF87a", {"image/gif"}),
    (b"GIF89a", {"image/gif"}),
    (b"RIFF", {"image/webp"}),
    (b"\x00\x00\x00\x18ftyp", {"video/mp4", "video/quicktime"}),
    (b"ftyp", {"video/mp4", "video/quicktime"}),
    (b"\x00\x00\x00\x1cftyp", {"video/mp4", "video/quicktime"}),
    (b"\x1a\x45\xdf\xa3", {"video/webm", "video/x-matroska"}),
    (b"OggS", {"video/ogg"}),
]

_MAX_NAME = 120


def sniff_mime(head: bytes) -> str | None:
    # MP4/MOV: ftyp box at offset 4 (size field is 4 bytes, then 'ftyp')
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "video/mp4"
    for magic, mimes in _MAGIC:
        if head.startswith(magic):
            # webp check: RIFF....WEBP at offset 8
            if magic == b"RIFF":
                if head[8:12] == b"WEBP":
                    return "image/webp"
                continue
            # mp4: ftyp box at offset 4 — handled above for any box size
            if magic == b"ftyp":
                return "video/mp4"
            return next(iter(mimes))
    return None


def mime_is_allowed(mime: str | None, allowed: set[str] | None) -> bool:
    if mime is None:
        return False
    if allowed is None:
        return True
    return mime in allowed


class DownloadError(Exception):
    pass


async def stream_download(
    client: httpx.AsyncClient,
    url: str,
    dest_dir: Path,
    filename: str,
    *,
    max_size_mb: int = 2048,
    timeout_seconds: int = 300,
    allowed_mimes: set[str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Path:
    """Stream ``url`` to ``dest_dir / filename``.

    Writes to ``<filename>.part`` first and only renames to the final name
    after the full download succeeds and passes validation. Raises
    DownloadError on failure and cleans up the partial file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / sanitize_filename(filename, _MAX_NAME)
    part_path = dest_dir / (final_path.name + ".part")
    max_bytes = max_size_mb * 1024 * 1024

    part_path.unlink(missing_ok=True)
    try:
        url = validate_remote_url(url, allow_private=False)
        headers = {"User-Agent": "FreeBatchStudio/1.0", **(extra_headers or {})}
        async with client.stream(
            "GET", url, headers=headers,
            timeout=httpx.Timeout(timeout_seconds), follow_redirects=True,
        ) as resp:
            if resp.status_code >= 400:
                raise DownloadError(f"download failed: HTTP {resp.status_code} for {url}")
            content_length = resp.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise DownloadError(
                            f"download too large: {content_length} bytes > {max_bytes}"
                        )
                except ValueError:
                    pass

            total = 0
            head = bytearray()
            with open(part_path, "wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadError(
                            f"download exceeded max size {max_size_mb}MB"
                        )
                    if len(head) < 1024:
                        head.extend(chunk[: 1024 - len(head)])
                    fh.write(chunk)

        if total == 0:
            raise DownloadError(f"empty download from {url}")
        mime = sniff_mime(bytes(head))
        if not mime_is_allowed(mime, allowed_mimes):
            raise DownloadError(
                f"unexpected content type {mime!r} from {url} "
                f"(allowed: {allowed_mimes or 'any'})"
            )
        part_path.replace(final_path)
        logger.info("downloaded %s (%d bytes, %s)", final_path.name, total, mime)
        return final_path
    except (DownloadError, httpx.HTTPError, SecurityError) as exc:
        part_path.unlink(missing_ok=True)
        if isinstance(exc, DownloadError):
            raise
        raise DownloadError(f"download failed for {url}: {exc}") from exc
    except Exception:
        part_path.unlink(missing_ok=True)
        raise


def write_base64_image(
    b64: str,
    dest_dir: Path,
    filename: str,
    *,
    max_size_mb: int = 50,
    allowed_mimes: set[str] | None = None,
) -> Path:
    """Decode a base64 image body and write it to disk with validation."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / sanitize_filename(filename, _MAX_NAME)
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DownloadError("invalid base64 payload") from exc
    max_bytes = max_size_mb * 1024 * 1024
    if not data:
        raise DownloadError("empty base64 payload")
    if len(data) > max_bytes:
        raise DownloadError(f"payload exceeded {max_size_mb}MB")
    mime = sniff_mime(data[:1024])
    if not mime_is_allowed(mime, allowed_mimes):
        raise DownloadError(f"unexpected content type {mime!r} in base64 payload")
    final_path.write_bytes(data)
    return final_path


def cleanup_part_files(dest_dir: Path) -> int:
    """Remove leftover .part files in a directory. Returns count removed."""
    removed = 0
    if not dest_dir.exists():
        return 0
    for part in dest_dir.glob("*.part"):
        try:
            part.unlink()
            removed += 1
        except OSError:
            logger.warning("could not remove %s", part)
    return removed


def parse_content_type(header: str | None) -> str | None:
    if not header:
        return None
    match = re.match(r"\s*([^/;\s]+/[^/;\s]+)", header)
    return match.group(1).lower() if match else None
