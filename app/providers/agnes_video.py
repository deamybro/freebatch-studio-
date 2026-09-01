"""Agnes Video V2.0 provider (asynchronous).

Official API (verified against wiki.agnes-ai.com/en/docs/agnes-video-v20):
  POST https://apihub.agnes-ai.com/v1/videos          create task
  GET  https://apihub.agnes-ai.com/agnesapi?video_id=  recommended result lookup
  - task statuses: queued | in_progress | completed | failed
  - num_frames <= 441 and follows the 8n+1 rule
  - on completion the MP4 URL is in metadata.url
  - the API normalizes resolution; use size / metadata.size_mapping as the
    source of truth (requested vs actual may differ)
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_agnes_api_key
from app.constants import (
    AGNES_VIDEO_FRAME_PRESETS,
    DURATION_PRESETS,
    PROVIDER_AGNES_VIDEO,
    VIDEO_RATIO_DIMENSIONS,
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
CREATE_ENDPOINT = "/v1/videos"
RESULT_ENDPOINT = "/agnesapi"
MODEL = "agnes-video-v2.0"
DOC_URL = "https://wiki.agnes-ai.com/en/docs/agnes-video-v20"

VALID_MIMES = {"video/mp4", "video/quicktime"}


def clamp_frames(raw: int | None) -> int:
    """Snap a frame count to the nearest valid 8n+1 value within [1, 441]."""
    if raw is None:
        return 121
    raw = max(1, min(441, int(raw)))
    n = round((raw - 1) / 8)
    n = max(0, min(55, n))
    return 8 * n + 1


class AgnesVideoProvider(BaseProvider):
    name = PROVIDER_AGNES_VIDEO
    display_name = "Agnes Video"
    media_types = ("video",)
    supports_async = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._auth_verified: bool | None = None

    def is_configured(self) -> bool:
        return bool(get_agnes_api_key())

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {get_agnes_api_key()}",
            "Content-Type": "application/json",
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            **super().capabilities(),
            "models": [MODEL],
            "job_types": ["text_to_video", "image_to_video", "keyframes"],
            "duration_presets": DURATION_PRESETS,
            "frame_presets": list(AGNES_VIDEO_FRAME_PRESETS),
            "max_frames": 441,
            "frame_rule": "8n + 1",
            "fps_range": [1, 60],
            "tiers": ["480p", "720p", "1080p"],
            "ratios": list(VIDEO_RATIO_DIMENSIONS),
            "doc_url": DOC_URL,
            "asynchronous": True,
            "notes": (
                "image_to_video and keyframes require PUBLICLY ACCESSIBLE HTTPS "
                "image URLs. Local files cannot be sent directly."
            ),
        }

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
            if not reachable and resp.status_code in (401, 403):
                return HealthStatus(
                    state="authenticated",
                    detail="reachable but authentication rejected (HTTP "
                    + str(resp.status_code) + ")",
                    reachable=True, authenticated=False,
                )
            return HealthStatus(
                state="reachable",
                detail="API gateway reachable; auth not yet verified",
                reachable=reachable, authenticated=bool(self._auth_verified),
            )
        except httpx.HTTPError as exc:
            return HealthStatus(
                state="unavailable", detail=f"connection error: {exc}",
                reachable=False, authenticated=None,
            )

    async def verify(self) -> HealthStatus:
        """Prove the key works with a minimal text-to-video task creation.

        Only creates a task; does not wait for or download the video.
        """
        if not self.is_configured():
            return HealthStatus(
                state="configured", detail="API key not configured",
                reachable=None, authenticated=False,
            )
        body = {
            "model": MODEL,
            "prompt": "A brief clip of a cat stretching.",
            "num_frames": 81,
            "frame_rate": 24,
            "width": 832,
            "height": 448,
        }
        try:
            resp = await self.client.post(
                f"{BASE_URL}{CREATE_ENDPOINT}", headers=self._headers(),
                json=body, timeout=httpx.Timeout(60),
            )
            if resp.status_code >= 400:
                raise classify_http_error(httpx.HTTPStatusError(
                    f"verify failed: {resp.status_code}", request=resp.request,
                    response=resp,
                ))
            self._auth_verified = True
            return HealthStatus(
                state="authenticated", detail="task creation succeeded",
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
                detail=f"task creation failed ({exc.category})",
                reachable=True, authenticated=False,
            )

    # -- validation -----------------------------------------------------
    async def validate_job(self, job: dict) -> None:
        if not self.is_configured():
            raise ProviderError(
                "auth", "AGNES_API_KEY is not configured in the environment"
            )
        if not (job.get("prompt") or "").strip():
            raise ProviderError("invalid_request", "prompt is required")
        settings = job.get("requested_settings") or {}
        job_type = job.get("type", "text_to_video")
        if job_type == "image_to_video" and not (settings.get("image_url") or "").strip():
            raise ProviderError(
                "invalid_request",
                "image_to_video requires a public HTTPS image_url",
            )
        if job_type == "keyframes":
            keyframes = settings.get("keyframe_urls") or []
            if not keyframes:
                raise ProviderError(
                    "invalid_request", "keyframes requires keyframe_urls"
                )
        frames = clamp_frames(settings.get("num_frames"))
        if frames not in AGNES_VIDEO_FRAME_PRESETS:
            raise ProviderError(
                "invalid_request",
                f"num_frames must follow 8n+1 within 1..441, got {frames}",
            )

    # -- submit ---------------------------------------------------------
    async def submit(self, job: dict) -> SubmitResult:
        await self.validate_job(job)
        settings = job.get("requested_settings") or {}
        job_type = job.get("type", "text_to_video")

        frames = clamp_frames(settings.get("num_frames"))
        fps = int(settings.get("fps") or 24)
        fps = max(1, min(60, fps))
        ratio = settings.get("ratio") or "16:9"
        tier = settings.get("tier") or "720p"
        width = settings.get("width")
        height = settings.get("height")
        if (not width or not height) and (ratio, tier) in VIDEO_RATIO_DIMENSIONS:
            width, height = VIDEO_RATIO_DIMENSIONS[(ratio, tier)]

        body: dict[str, Any] = {
            "model": MODEL,
            "prompt": job.get("prompt", ""),
            "num_frames": frames,
            "frame_rate": fps,
            "width": width,
            "height": height,
        }
        if settings.get("negative_prompt"):
            body["negative_prompt"] = settings["negative_prompt"]
        if settings.get("seed") is not None:
            body["seed"] = int(settings["seed"])

        extra: dict[str, Any] = {}
        if job_type == "image_to_video":
            body["image"] = settings.get("image_url")
        elif job_type == "keyframes":
            extra["image"] = list(settings.get("keyframe_urls") or [])
            extra["mode"] = "keyframes"
            body["extra_body"] = extra
        elif job_type == "text_to_video" and settings.get("image_url"):
            body["image"] = settings.get("image_url")

        await self._rate_acquire("agnes_video:default")

        self._log(job.get("id"), "submit", model=MODEL, job_type=job_type,
                  frames=frames, fps=fps, width=width, height=height)
        try:
            resp = await self.client.post(
                f"{BASE_URL}{CREATE_ENDPOINT}", headers=self._headers(),
                json=body,
                timeout=httpx.Timeout(self._setting("request_timeout_seconds", 300)),
            )
            if resp.status_code >= 400:
                raise classify_http_error(httpx.HTTPStatusError(
                    f"agres video create failed: {resp.status_code}",
                    request=resp.request, response=resp,
                ))
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "timeout", "Agnes Video create timed out", http_body=str(exc)
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "network", f"connection error: {exc}", http_body=str(exc)
            ) from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_rejected", "invalid JSON in create response"
            ) from exc

        video_id = data.get("video_id") or data.get("task_id")
        task_id = data.get("task_id") or data.get("id")
        if not video_id:
            raise ProviderError(
                "provider_rejected", "create response missing video_id",
                http_body=str(data)[:1000],
            )

        actual = {
            "num_frames": frames,
            "frame_rate": fps,
            "width": width,
            "height": height,
            "reported_size": data.get("size"),
            "reported_seconds": data.get("seconds"),
            "status": data.get("status"),
        }
        self._log(job.get("id"), "submit_ok", video_id=video_id, task_id=task_id,
                  size=data.get("size"), status=data.get("status"))
        return SubmitResult(
            status="queued_remote",
            remote_task_id=task_id,
            remote_video_id=video_id,
            actual_settings=actual,
            model=MODEL,
            sanitized_payload=self._sanitize(body),
            sanitized_response={
                "id": data.get("id"),
                "task_id": data.get("task_id"),
                "video_id": data.get("video_id"),
                "status": data.get("status"),
                "progress": data.get("progress"),
                "size": data.get("size"),
                "seconds": data.get("seconds"),
            },
        )

    # -- poll -----------------------------------------------------------
    async def poll(self, job: dict) -> PollResult:
        video_id = job.get("remote_video_id") or job.get("remote_task_id")
        if not video_id:
            raise ProviderError(
                "not_found", "no remote video_id stored for this job"
            )
        await self._rate_acquire("agnes_video:poll")
        params = {"video_id": video_id, "model_name": MODEL}
        self._log(job.get("id"), "poll", video_id=video_id)
        try:
            resp = await self.client.get(
                f"{BASE_URL}{RESULT_ENDPOINT}", params=params,
                headers=self._headers(),
                timeout=httpx.Timeout(self._setting("poll_timeout_seconds", 60)),
            )
            if resp.status_code >= 400:
                raise classify_http_error(httpx.HTTPStatusError(
                    f"agres video poll failed: {resp.status_code}",
                    request=resp.request, response=resp,
                ))
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "timeout", "Agnes Video poll timed out", http_body=str(exc)
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "network", f"connection error: {exc}", http_body=str(exc)
            ) from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_rejected", "invalid JSON in poll response"
            ) from exc

        status = (data.get("status") or "").lower()
        metadata = data.get("metadata") or {}
        size_mapping = metadata.get("size_mapping") or {}
        actual = {
            "reported_size": data.get("size"),
            "reported_seconds": data.get("seconds"),
            "progress": data.get("progress"),
            "size_mapping": size_mapping,
            "requested": {
                "width": size_mapping.get("requested_width"),
                "height": size_mapping.get("requested_height"),
            },
            "actual": {
                "width": size_mapping.get("width"),
                "height": size_mapping.get("height"),
                "resolution": size_mapping.get("resolution"),
                "ratio": size_mapping.get("ratio"),
            },
        }

        if status == "completed":
            url = metadata.get("url") or data.get("url")
            if not url:
                raise ProviderError(
                    "provider_rejected",
                    "video marked completed but metadata.url is missing",
                    http_body=str(data)[:1000],
                )
            self._log(job.get("id"), "poll_completed", url=url, size=data.get("size"))
            return PollResult(
                status="completed",
                progress=100,
                remote_output_url=url,
                actual_settings=actual,
                model=MODEL,
                sanitized_response={
                    "status": status, "size": data.get("size"),
                    "seconds": data.get("seconds"),
                    "progress": data.get("progress"),
                },
            )
        if status == "failed":
            err = data.get("error") or {}
            message = err.get("message") if isinstance(err, dict) else str(err)
            return PollResult(
                status="failed", error=message or "video generation failed",
                sanitized_response={"status": status},
            )
        if status == "queued":
            return PollResult(
                status="queued", progress=data.get("progress"),
                actual_settings=actual,
                sanitized_response={"status": status, "progress": data.get("progress")},
            )
        if status == "in_progress":
            return PollResult(
                status="processing", progress=data.get("progress"),
                actual_settings=actual,
                sanitized_response={"status": status, "progress": data.get("progress")},
            )
        return PollResult(
            status="processing", progress=data.get("progress"),
            sanitized_response={"status": status or "unknown"},
        )

    async def cancel(self, job: dict) -> None:
        # Agnes Video's official API does not document a cancel endpoint.
        self._log(
            job.get("id"), "cancel",
            note="no documented cancel endpoint; local polling stopped, "
            "remote computation may still finish",
        )

    def output_mime(self) -> set[str]:
        return VALID_MIMES

    def expected_extension(self, job: dict, actual: dict) -> str:
        return "mp4"
