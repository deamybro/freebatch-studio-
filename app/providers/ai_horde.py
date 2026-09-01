"""AI Horde image provider (community, free, asynchronous).

Official V2 REST API (verified against aihorde.net/api):
  GET    /api/v2/status/models?type=image   active models
  POST   /api/v2/generate/async             submit generation
  GET    /api/v2/generate/check/{id}        status check (no images)
  GET    /api/v2/generate/status/{id}       full status (images as base64)
  DELETE /api/v2/generate/status/{id}       cancel unfinished request
  GET    /api/v2/status/heartbeat           availability
  Auth:  apikey header (anonymous key: 0000000000)

AI Horde is an IMAGE-ONLY fallback provider in FreeBatch Studio. It does not
support the generative-video workflow and we do not pretend otherwise.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_horde_api_key
from app.constants import (
    HORDE_ANONYMOUS_KEY,
    HORDE_RATIO_DIMENSIONS,
    PROVIDER_AI_HORDE,
)
from app.providers.base import (
    BaseProvider,
    HealthStatus,
    PollResult,
    ProviderError,
    SubmitResult,
    classify_http_error,
)

BASE_URL = "https://aihorde.net/api"
ANON_KEY = HORDE_ANONYMOUS_KEY
DOC_URL = "https://aihorde.net/api"
VALID_MIMES = {"image/png", "image/jpeg", "image/webp"}


class AIHordeProvider(BaseProvider):
    name = PROVIDER_AI_HORDE
    display_name = "AI Horde"
    media_types = ("image",)
    supports_async = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._models_cache: list[dict[str, Any]] = []
        self._models_ts = 0.0

    # -- config ---------------------------------------------------------
    def is_configured(self) -> bool:
        # Anonymous access always works; a user-supplied key is optional.
        return True

    @property
    def anonymous(self) -> bool:
        key = get_horde_api_key()
        return not key or key == ANON_KEY

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": get_horde_api_key(),
            "Content-Type": "application/json",
        }

    # -- models ---------------------------------------------------------
    async def active_models(self, force: bool = False) -> list[dict[str, Any]]:
        import time as _time

        if self._models_cache and not force and (_time.time() - self._models_ts) < 300:
            return self._models_cache
        try:
            resp = await self.client.get(
                f"{BASE_URL}/v2/status/models", params={"type": "image"},
                headers=self._headers(), timeout=httpx.Timeout(30),
            )
            if resp.status_code >= 400:
                raise classify_http_error(httpx.HTTPStatusError(
                    f"horde models failed: {resp.status_code}",
                    request=resp.request, response=resp,
                ))
            data = resp.json()
            models = [m for m in data if isinstance(m, dict)]
            self._models_cache = models
            self._models_ts = _time.time()
            return models
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(
                "network", f"could not fetch active models: {exc}"
            ) from exc

    def _pick_model(self, models: list[dict[str, Any]], preferred: str | None) -> str | None:
        if not models:
            return None
        if preferred:
            for m in models:
                if m.get("name") == preferred:
                    return preferred
        available = [m for m in models if m.get("count", 0) > 0]
        pool = available or models
        # Prefer models with workers and low queued load.
        pool = sorted(pool, key=lambda m: (m.get("queued", 0) or 0) / max(1, m.get("count", 1)))
        return pool[0].get("name")

    async def resolve_model(self, preferred: str | None = None) -> str:
        models = await self.active_models()
        model = self._pick_model(models, preferred)
        if not model:
            raise ProviderError(
                "provider_rejected", "no active AI Horde models available"
            )
        return model

    # -- capabilities ---------------------------------------------------
    def capabilities(self) -> dict[str, Any]:
        return {
            **super().capabilities(),
            "models": "dynamic",
            "job_types": ["text_to_image"],
            "ratio_dimensions": HORDE_RATIO_DIMENSIONS,
            "doc_url": DOC_URL,
            "anonymous": self.anonymous,
            "anonymous_note": "Anonymous AI Horde requests have lowest queue priority.",
            "asynchronous": True,
            "notes": "IMAGE fallback provider only (V1); no video support.",
        }

    # -- health ---------------------------------------------------------
    async def healthcheck(self) -> HealthStatus:
        try:
            resp = await self.client.get(
                f"{BASE_URL}/v2/status/models", params={"type": "image"},
                headers=self._headers(), timeout=httpx.Timeout(5),
            )
            if resp.status_code == 200:
                try:
                    models = resp.json()
                except ValueError:
                    models = []
                n_models = len(models) if isinstance(models, list) else 0
                return HealthStatus(
                    state="reachable",
                    detail=f"reachable; {n_models} active models",
                    reachable=True, authenticated=True,
                )
            if resp.status_code == 429:
                return HealthStatus(
                    state="rate_limited", detail="rate limited by AI Horde",
                    reachable=True, authenticated=True, rate_limited=True,
                )
            if resp.status_code in (401, 403):
                return HealthStatus(
                    state="authenticated", detail="API key rejected",
                    reachable=True, authenticated=False,
                )
            return HealthStatus(
                state="unavailable", detail=f"HTTP {resp.status_code}",
                reachable=False, authenticated=None,
            )
        except httpx.HTTPError as exc:
            return HealthStatus(
                state="unavailable", detail=f"connection error: {exc}",
                reachable=False, authenticated=None,
            )

    # -- validation -----------------------------------------------------
    async def validate_job(self, job: dict) -> None:
        prompt = (job.get("prompt") or "").strip()
        if not prompt:
            raise ProviderError("invalid_request", "prompt is required")
        settings = job.get("requested_settings") or {}
        job_type = job.get("type", "text_to_image")
        if job_type != "text_to_image":
            raise ProviderError(
                "provider_rejected",
                "AI Horde supports text_to_image only; image_to_image/multi_image "
                "would silently ignore reference images (video is not supported)",
            )
        w, h = self._dimensions(settings)
        if (w, h) not in set(HORDE_RATIO_DIMENSIONS.values()):
            raise ProviderError(
                "invalid_request",
                f"unsupported AI Horde dimensions {w}x{h}; "
                f"choose one of {sorted(set(HORDE_RATIO_DIMENSIONS.values()))}",
            )

    def _dimensions(self, settings: dict) -> tuple[int, int]:
        w = settings.get("width")
        h = settings.get("height")
        if w and h:
            return int(w), int(h)
        ratio = settings.get("ratio") or "1:1"
        if ratio in HORDE_RATIO_DIMENSIONS:
            return HORDE_RATIO_DIMENSIONS[ratio]
        return 1024, 1024

    # -- submit ---------------------------------------------------------
    async def submit(self, job: dict) -> SubmitResult:
        await self.validate_job(job)
        settings = job.get("requested_settings") or {}
        model = await self.resolve_model(job.get("model"))
        width, height = self._dimensions(settings)
        seed = settings.get("seed")
        negative = settings.get("negative_prompt")

        params: dict[str, Any] = {
            "width": width,
            "height": height,
            "steps": int(self._setting("horde_steps", 20)),
            "cfg_scale": 7.0,
            "denoising_strength": 0.75,
        }
        if seed is not None:
            params["seed"] = int(seed)
        if negative:
            params["negativeprompt"] = negative

        body: dict[str, Any] = {
            "prompt": job.get("prompt", ""),
            "params": params,
            "models": [model],
            "nsfw": False,
            "trusted_workers": False,
            "slow_workers": True,
        }

        await self._rate_acquire("ai_horde:submit")

        self._log(job.get("id"), "submit", model=model, width=width, height=height,
                  anonymous=self.anonymous)
        try:
            resp = await self.client.post(
                f"{BASE_URL}/v2/generate/async", headers=self._headers(),
                json=body,
                timeout=httpx.Timeout(self._setting("request_timeout_seconds", 300)),
            )
            if resp.status_code >= 400:
                raise classify_http_error(httpx.HTTPStatusError(
                    f"horde submit failed: {resp.status_code}",
                    request=resp.request, response=resp,
                ))
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "timeout", "AI Horde submit timed out", http_body=str(exc)
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "network", f"connection error: {exc}", http_body=str(exc)
            ) from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_rejected", "invalid JSON in horde submit response"
            ) from exc

        task_id = data.get("id")
        if not task_id:
            raise ProviderError(
                "provider_rejected", "horde submit response missing id",
                http_body=str(data)[:1000],
            )
        actual = {"model": model, "width": width, "height": height,
                  "anonymous": self.anonymous}
        self._log(job.get("id"), "submit_ok", task_id=task_id, model=model)
        return SubmitResult(
            status="queued_remote",
            remote_task_id=task_id,
            actual_settings=actual,
            model=model,
            sanitized_payload=self._sanitize({"models": [model], "params": params}),
            sanitized_response={"id": task_id, "message": data.get("message")},
        )

    # -- poll -----------------------------------------------------------
    async def poll(self, job: dict) -> PollResult:
        task_id = job.get("remote_task_id")
        if not task_id:
            raise ProviderError("not_found", "no remote task id stored")
        await self._rate_acquire("ai_horde:poll")
        self._log(job.get("id"), "poll", task_id=task_id)
        try:
            check = await self.client.get(
                f"{BASE_URL}/v2/generate/check/{task_id}", headers=self._headers(),
                timeout=httpx.Timeout(30),
            )
            if check.status_code >= 400:
                raise classify_http_error(httpx.HTTPStatusError(
                    f"horde check failed: {check.status_code}",
                    request=check.request, response=check,
                ))
            check_data = check.json()
            if check_data.get("faulted"):
                return PollResult(
                    status="failed",
                    error="AI Horde job faulted (worker error)",
                    sanitized_response={"faulted": True},
                )
            if not check_data.get("done"):
                return PollResult(
                    status="processing",
                    progress=0,
                    sanitized_response={
                        "waiting": check_data.get("waiting"),
                        "queue_position": check_data.get("queue_position"),
                        "is_possible": check_data.get("is_possible"),
                    },
                )
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "AI Horde check timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("network", f"connection error: {exc}") from exc

        try:
            status = await self.client.get(
                f"{BASE_URL}/v2/generate/status/{task_id}", headers=self._headers(),
                timeout=httpx.Timeout(60),
            )
            if status.status_code >= 400:
                raise classify_http_error(httpx.HTTPStatusError(
                    f"horde status failed: {status.status_code}",
                    request=status.request, response=status,
                ))
            data = status.json()
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "AI Horde status timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("network", f"connection error: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(
                "provider_rejected", "invalid JSON in horde status response"
            ) from exc

        generations = data.get("generations") or []
        if not generations:
            return PollResult(
                status="failed", error="AI Horde reported done but returned no images"
            )
        gen = generations[0]
        if gen.get("censored"):
            return PollResult(
                status="failed",
                error="AI Horde censored this generation (content policy)",
                sanitized_response={"censored": True},
            )
        b64 = gen.get("img")
        if not b64:
            return PollResult(
                status="failed", error="AI Horde generation has no image data"
            )
        actual = {
            "model": gen.get("model"),
            "seed": gen.get("seed"),
            "worker_id": gen.get("worker_id") or "",
            "anonymous": self.anonymous,
        }
        self._log(job.get("id"), "poll_completed", model=gen.get("model"),
                  seed=gen.get("seed"))
        return PollResult(
            status="completed",
            progress=100,
            b64_output=b64,
            actual_settings=actual,
            model=gen.get("model") or job.get("model"),
            sanitized_response={
                "done": True, "seed": gen.get("seed"), "model": gen.get("model"),
                "censored": gen.get("censored"),
            },
        )

    async def cancel(self, job: dict) -> None:
        task_id = job.get("remote_task_id")
        if not task_id:
            return
        self._log(job.get("id"), "cancel", task_id=task_id)
        try:
            resp = await self.client.delete(
                f"{BASE_URL}/v2/generate/status/{task_id}", headers=self._headers(),
                timeout=httpx.Timeout(30),
            )
            self._log(job.get("id"), "cancel_result", status=resp.status_code)
        except httpx.HTTPError as exc:
            self._log(job.get("id"), "cancel_error", detail=str(exc))

    def output_mime(self) -> set[str]:
        return VALID_MIMES

    def expected_extension(self, job: dict, actual: dict) -> str:
        return "png"
