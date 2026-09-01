"""Pydantic API schemas (request/response payloads)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.constants import (
    AGNES_IMAGE_RATIOS,
    AGNES_IMAGE_TIERS,
    DURATION_PRESETS,
    JobType,
)


class JobRequest(BaseModel):
    job_index: int = 0
    type: JobType
    prompt: str
    negative_prompt: str | None = None
    provider: str
    model: str | None = None
    fallback_provider: str | None = None
    max_attempts: int = 3
    requested_settings: dict[str, Any] = Field(default_factory=dict)


class BatchCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    media_type: Literal["image", "video"]
    jobs: list[JobRequest] = Field(default_factory=list)
    pricing_acknowledged: bool = False
    fallback_provider: str | None = None


class ImageBatchDefaults(BaseModel):
    provider: str = "agnes_image"
    model: str = "agnes-image-2.1-flash"
    fallback_provider: str | None = "ai_horde"
    size: str = "1K"
    ratio: str = "1:1"
    output_format: Literal["url", "b64_json"] = "url"
    max_attempts: int = 3

    @field_validator("size")
    @classmethod
    def _size(cls, v: str) -> str:
        v = v.upper()
        if v not in AGNES_IMAGE_TIERS:
            raise ValueError(f"size must be one of {AGNES_IMAGE_TIERS}")
        return v

    @field_validator("ratio")
    @classmethod
    def _ratio(cls, v: str) -> str:
        if v not in AGNES_IMAGE_RATIOS:
            raise ValueError(f"ratio must be one of {AGNES_IMAGE_RATIOS}")
        return v


class VideoBatchDefaults(BaseModel):
    provider: str = "agnes_video"
    model: str = "agnes-video-v2.0"
    fallback_provider: str | None = None
    kind: Literal["text_to_video", "image_to_video", "keyframes"] = "text_to_video"
    duration: Literal["3s", "5s", "10s", "18s"] = "5s"
    fps: int = 24
    ratio: str = "16:9"
    tier: Literal["480p", "720p", "1080p"] = "720p"
    seed: int | None = None
    negative_prompt: str | None = None
    image_url: str | None = None
    keyframe_urls: list[str] = Field(default_factory=list)
    max_attempts: int = 3

    def num_frames(self) -> int:
        return DURATION_PRESETS[self.duration]


class ParseRequest(BaseModel):
    """Parsed batch preview request."""

    media_type: Literal["image", "video"]
    text: str | None = None
    csv: str | None = None
    defaults_image: ImageBatchDefaults | None = None
    defaults_video: VideoBatchDefaults | None = None


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class HealthResponse(BaseModel):
    provider: str
    display_name: str
    configured: bool
    enabled: bool
    reachable: bool | None = None
    authenticated: bool | None = None
    rate_limited: bool | None = None
    detail: str = ""
    checked_at: str | None = None


class ProviderInfoResponse(BaseModel):
    name: str
    display_name: str
    media_types: list[str]
    enabled: bool
    configured: bool
    capabilities: dict[str, Any]
    health: HealthResponse | None = None
    pricing: dict[str, Any] | None = None


class JobOut(BaseModel):
    id: int
    job_index: int
    type: str
    prompt: str
    negative_prompt: str | None
    provider: str
    model: str | None
    fallback_provider: str | None
    requested_settings: dict[str, Any]
    actual_settings: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    remote_task_id: str | None
    remote_video_id: str | None
    remote_output_url: str | None
    local_output_path: str | None
    error_type: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    updated_at: str


class BatchOut(BaseModel):
    id: int
    name: str
    media_type: str
    status: str
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    pricing_acknowledged: bool
    fallback_provider: str | None
    created_at: str
    updated_at: str


class DashboardStats(BaseModel):
    batches_today: int
    total_batches: int
    jobs_pending: int
    jobs_queued_remote: int
    jobs_processing: int
    jobs_polling: int
    jobs_completed: int
    jobs_failed: int
    jobs_cancelled: int
    free_only_mode: bool
    providers_online: int
    providers_offline: int
    pricing_stale: bool
    storage_used_mb: float | None = None
