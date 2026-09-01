"""Agnes Image 2.1 Flash provider.

Official API (verified against wiki.agnes-ai.com/en/docs/agnes-image-21-flash):
  POST https://apihub.agnes-ai.com/v1/images/generations
  - model, prompt, size (tier 1K/2K/3K/4K or exact WxH), ratio (optional)
  - response_format goes INSIDE extra_body (top-level is a documented 400 error)
  - image inputs go inside extra_body.image (array)
  - text-to-image base64 uses top-level return_base64: true
  - response: data[0].url or data[0].b64_json
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_agnes_api_key
from app.constants import (
    AGNES_IMAGE_DIMENSIONS,
    AGNES_IMAGE_RATIOS,
    AGNES_IMAGE_TIERS,
    PROVIDER_AGNES_IMAGE,
)
from app.providers.base import (
    BaseProvider,
    HealthStatus,
    PollResult,
    ProviderError,
    SubmitResult,
    classify_http_error,
)

BASE_URL = "https://apihub.agnes-ai.com"
ENDPOINT = "/v1/images/generations"
MODEL = "agnes-image-2.1-flash"
DOC_URL = "https://wiki.agnes-ai.com/en/docs/agnes-image-21-flash"

VALID_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class AgnesImageProvider(BaseProvider):
    name = PROVIDER_AGNES_IMAGE
    display_name = "Agnes Image"
    media_types = ("image",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._auth_verified: bool | None = None

    # -- config ---------------------------------------------------------
    def is_configured(self) -> bool:
        return bool(get_agnes_api_key())

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {get_agnes_api_key()}",
            "Content-Type": "application/json",
        }

    # -- capabilities ---------------------------------------------------
    def capabilities(self) -> dict[str, Any]:
        return {
            **super().capabilities(),
            "models": [MODEL],
            "job_types": ["text_to_image", "image_to_image", "multi_image"],
            "sizes": list(AGNES_IMAGE_TIERS),
            "ratios": list(AGNES_IMAGE_RATIOS),
            "output_formats": ["url", "b64_json"],
            "dimensions": AGNES_IMAGE_DIMENSIONS,
            "doc_url": DOC_URL,
            "supports_base64": True,
            "notes": "response_format must be placed inside extra_body",
        }

    # -- health ---------------------------------------------------------
    async def healthcheck(self) -> HealthStatus:
        if not self.is_configured():
            return HealthStatus(
                state="configured", detail="API key not configured",
                reachable=None, authenticated=False,
            )
        try:
            resp = await self.client.get(
                f"{BASE_URL}/v1/models", headers=self._headers(),
                timeout=httpx.Timeout(5),
            )
            reachable = resp.status_code == 200
            auth_state = bool(self._auth_verified)
            if not reachable and resp.status_code in (401, 403):
                return HealthStatus(
                    state="authenticated",
                    detail="reachable but authentication rejected (HTTP "
                    + str(resp.status_code) + ")",
                    reachable=True, authenticated=False,
                )
            state = "reachable"
            if auth_state:
                state = "authenticated"
            return HealthStatus(
                state=state,
                detail="API gateway reachable"
                + ("" if auth_state else "; auth not yet verified"),
                reachable=reachable,
                authenticated=auth_state,
            )
        except httpx.HTTPError as exc:
            return HealthStatus(
                state="unavailable", detail=f"connection error: {exc}",
                reachable=False, authenticated=None,
            )

    async def verify(self) -> HealthStatus:
        """Run one minimal 1K free generation to prove the key + pricing."""
        if not self.is_configured():
            return HealthStatus(
                state="configured", detail="API key not configured",
                reachable=None, authenticated=False,
            )
        try:
            payload = {
                "model": MODEL,
                "prompt": "A single red circle on a white background",
                "size": "1K",
                "ratio": "1:1",
                "extra_body": {"response_format": "url"},
            }
            resp = await self.client.post(
                f"{BASE_URL}{ENDPOINT}", headers=self._headers(), json=payload,
                timeout=httpx.Timeout(180),
            )
            if resp.status_code >= 400:
                raise classify_http_error(httpx.HTTPStatusError(
                    f"verify failed: {resp.status_code}", request=resp.request,
                    response=resp,
                ))
            data = resp.json().get("data") or []
            if not data:
                raise ProviderError(
                    "provider_rejected", "verify response had no data"
                )
            self._auth_verified = True
            return HealthStatus(
                state="authenticated", detail="1K generation succeeded",
                reachable=True, authenticated=True,
            )
        except httpx.HTTPError as exc:
            return HealthStatus(
                state="unavailable", detail=f"connection error: {exc}",
                reachable=False, authenticated=None,
            )
        except ProviderError as exc:
            self._auth_verified = False
            return HealthStatus(
                state="authenticated",
                detail=f"generation failed ({exc.category})",
                reachable=True, authenticated=False,
            )

    # -- validation -----------------------------------------------------
    async def validate_job(self, job: dict) -> None:
        if not self.is_configured():
            raise ProviderError(
                "auth", "AGNES_API_KEY is not configured in the environment"
            )
        prompt = (job.get("prompt") or "").strip()
        if not prompt:
            raise ProviderError("invalid_request", "prompt is required")
        settings = job.get("requested_settings") or {}
        job_type = job.get("type", "text_to_image")
        if job_type in ("image_to_image", "multi_image"):
            images = settings.get("image_urls") or []
            if not images:
                raise ProviderError(
                    "invalid_request",
                    f"{job_type} requires at least one image_url",
                )
        size = (settings.get("size") or "1K").upper()
        if size not in AGNES_IMAGE_TIERS:
            raise ProviderError(
                "invalid_request",
                f"size must be one of {AGNES_IMAGE_TIERS}, got {size!r}",
            )
        ratio = settings.get("ratio") or "1:1"
        if ratio not in AGNES_IMAGE_RATIOS:
            raise ProviderError(
                "invalid_request", f"ratio must be one of {AGNES_IMAGE_RATIOS}"
            )

    # -- submit ---------------------------------------------------------
    async def submit(self, job: dict) -> SubmitResult:
        await self.validate_job(job)
        settings = job.get("requested_settings") or {}
        job_type = job.get("type", "text_to_image")
        output_format = settings.get("output_format", "url")

        body: dict[str, Any] = {
            "model": MODEL,
            "prompt": job.get("prompt", ""),
        }
        size = (settings.get("size") or "1K").upper()
        ratio = settings.get("ratio") or "1:1"
        body["size"] = size
        body["ratio"] = ratio

        images: list[str] = list(settings.get("image_urls") or [])
        extra: dict[str, Any] = {}
        if job_type in ("image_to_image", "multi_image") and images:
            extra["image"] = images
        if output_format == "b64_json" and not images:
            # Docs: text-to-image base64 uses top-level return_base64.
            body["return_base64"] = True
        else:
            extra["response_format"] = "url" if output_format != "b64_json" else "b64_json"
        if extra:
            body["extra_body"] = extra

        tier_key = size.lower()
        await self._rate_acquire(f"agnes_image:{tier_key}")

        self._log(job.get("id"), "submit", model=MODEL, size=size, ratio=ratio,
                  job_type=job_type)
        try:
            resp = await self.client.post(
                f"{BASE_URL}{ENDPOINT}", headers=self._headers(), json=body,
                timeout=httpx.Timeout(self._setting("request_timeout_seconds", 300)),
            )
            if resp.status_code >= 400:
                raise classify_http_error(httpx.HTTPStatusError(
                    f"agres image failed: {resp.status_code}",
                    request=resp.request, response=resp,
                ))
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "timeout", "Agnes Image request timed out", http_body=str(exc)
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "network", f"connection error: {exc}", http_body=str(exc)
            ) from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_rejected", "invalid JSON in response"
            ) from exc

        items = data.get("data") or []
        if not items:
            raise ProviderError(
                "provider_rejected", "response contained no image data",
                http_body=str(data)[:1000],
            )
        first = items[0]
        url = first.get("url")
        b64 = first.get("b64_json")
        if not url and not b64:
            raise ProviderError(
                "provider_rejected", "image result has neither url nor b64_json",
                http_body=str(first)[:1000],
            )

        expected_dims = AGNES_IMAGE_DIMENSIONS.get((ratio, size), "unknown")
        actual = {
            "size": size,
            "ratio": ratio,
            "expected_dimensions": expected_dims,
            "output_format": output_format,
            "provider_reported": bool(url),
        }
        self._log(job.get("id"), "submit_ok", output_format=output_format,
                  has_url=bool(url), has_b64=bool(b64))
        return SubmitResult(
            status="processing",
            remote_output_url=url,
            b64_output=b64,
            actual_settings=actual,
            model=MODEL,
            sanitized_payload=self._sanitize(body),
            sanitized_response={"created": data.get("created")},
        )

    async def poll(self, job: dict) -> PollResult:
        raise ProviderError("invalid_request", "Agnes Image is synchronous")

    async def cancel(self, job: dict) -> None:
        # Agnes Image is synchronous; once submitted there is nothing to cancel.
        self._log(job.get("id"), "cancel", note="sync provider - no remote cancel")

    def output_mime(self) -> set[str]:
        return VALID_MIMES

    def expected_extension(self, job: dict, actual: dict) -> str:
        return "png"
