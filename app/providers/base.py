"""Provider abstraction.

UI/queue logic never talks to Agnes or AI Horde directly - only through the
BaseProvider interface. New providers can be added without touching the
database, queue or UI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

# Retryable error categories (technical availability issues).
RETRYABLE_CATEGORIES = {"rate_limit", "timeout", "network", "server", "quota"}
# Categories that must NEVER be retried or trigger fallback (safeguards).
NON_RETRYABLE_CATEGORIES = {
    "auth", "invalid_request", "content_policy", "provider_rejected", "not_found",
}


class ProviderError(Exception):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        retry_after: float | None = None,
        status_code: int | None = None,
        http_body: str = "",
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.retry_after = retry_after
        self.status_code = status_code
        self.http_body = http_body[:2000]

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE_CATEGORIES

    def __str__(self) -> str:  # pragma: no cover - trivial
        extra = f" (HTTP {self.status_code})" if self.status_code else ""
        ra = f", retry-after {self.retry_after}s" if self.retry_after else ""
        return f"{self.category}: {self.message}{extra}{ra}"


@dataclass
class HealthStatus:
    state: str  # configured | reachable | authenticated | rate_limited | unavailable | disabled | unknown
    detail: str = ""
    checked_at: str | None = None
    reachable: bool | None = None
    authenticated: bool | None = None
    rate_limited: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "reachable": self.reachable,
            "authenticated": self.authenticated,
            "rate_limited": self.rate_limited,
        }


@dataclass
class SubmitResult:
    status: str = "processing"  # "processing" (sync done) | "queued_remote" (async)
    remote_task_id: str | None = None
    remote_video_id: str | None = None
    remote_output_url: str | None = None
    b64_output: str | None = None
    actual_settings: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    sanitized_payload: dict[str, Any] = field(default_factory=dict)
    sanitized_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class PollResult:
    status: str  # queued | processing | completed | failed | cancelled
    progress: int | None = None
    remote_output_url: str | None = None
    b64_output: str | None = None
    actual_settings: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    error: str | None = None
    sanitized_response: dict[str, Any] = field(default_factory=dict)


def classify_http_error(exc: httpx.HTTPStatusError) -> ProviderError:
    status = exc.response.status_code
    body = exc.response.text[:2000]
    if status == 429:
        retry_after = _parse_retry_after(exc.response.headers.get("retry-after"))
        return ProviderError(
            "rate_limit", "rate limited (HTTP 429)", retry_after=retry_after,
            status_code=status, http_body=body,
        )
    if status == 401 or status == 403:
        return ProviderError(
            "auth", "authentication failed", status_code=status, http_body=body
        )
    if status == 400:
        return ProviderError(
            "invalid_request", "invalid request (HTTP 400)", status_code=status,
            http_body=body,
        )
    if status == 404:
        return ProviderError(
            "not_found", "resource not found (HTTP 404)", status_code=status,
            http_body=body,
        )
    if status in (500, 502, 503, 504):
        return ProviderError(
            "server", f"server error (HTTP {status})", status_code=status,
            http_body=body,
        )
    return ProviderError(
        "provider_rejected", f"provider rejected request (HTTP {status})",
        status_code=status, http_body=body,
    )


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.5, float(value))
    except ValueError:
        return None


def backoff_seconds(attempt: int, base: float = 2.0, cap: float = 60.0) -> float:
    import random

    exp = min(cap, base * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0.5, 1.5)
    return round(exp * jitter, 2)


class BaseProvider(ABC):
    name: str = "base"
    display_name: str = "Base"
    media_types: tuple[str, ...] = ("image", "video")
    supports_async: bool = False

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings_provider: Any,
        rate_limiter: Any,
        db: Any,
    ) -> None:
        self.client = client
        self.settings_provider = settings_provider
        self.rate_limiter = rate_limiter
        self.db = db

    def _setting(self, key: str, default: Any = None) -> Any:
        """Read a config value from either a dict-like or pydantic Settings.

        Production passes a pydantic ``Settings`` (attribute access); tests pass
        a dict-like stub (``.get``). Support both so providers never assume a
        ``.get`` method that pydantic models don't have.
        """
        sp = self.settings_provider
        if isinstance(sp, dict):
            return sp.get(key, default)
        return getattr(sp, key, default)

    @abstractmethod
    async def healthcheck(self) -> HealthStatus:
        ...

    @abstractmethod
    async def validate_job(self, job: dict) -> None:
        """Raise ProviderError if the job is not valid for this provider."""

    @abstractmethod
    async def submit(self, job: dict) -> SubmitResult:
        ...

    async def poll(self, job: dict) -> PollResult:
        raise ProviderError("invalid_request", "this provider does not support polling")

    async def cancel(self, job: dict) -> None:
        """Best-effort remote cancellation (no-op for providers without one)."""
        return None

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "media_types": list(self.media_types),
            "supports_async": self.supports_async,
        }

    def is_configured(self) -> bool:
        return True

    # -- helpers --------------------------------------------------------
    async def _rate_acquire(self, key: str) -> None:
        await self.rate_limiter.acquire(key)

    def _log(self, job_id: int | None, event: str, **meta: Any) -> None:
        self.db.log_event(job_id, self.name, event, meta)

    @staticmethod
    def _sanitize(payload: dict) -> dict:
        """Remove anything that could carry secrets from an event/metadata dump."""
        return {k: v for k, v in payload.items() if k not in ("authorization", "apikey")}


class DisabledProvider(BaseProvider):
    """Placeholder for future local providers (WanGP / ComfyUI)."""

    enabled = False
    reason = "Not configured"

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(
            state="disabled",
            detail=self.reason
            + " - intended for future local GPU hardware.",
        )

    async def validate_job(self, job: dict) -> None:
        raise ProviderError(
            "provider_rejected",
            self.reason + " - intended for future local GPU hardware.",
        )

    async def submit(self, job: dict) -> SubmitResult:
        raise ProviderError(
            "provider_rejected",
            self.reason + " - intended for future local GPU hardware.",
        )

    def is_configured(self) -> bool:
        return False
