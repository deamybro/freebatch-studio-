"""Provider registry and FREE-ONLY pricing metadata seeding."""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_agnes_api_key, get_horde_api_key
from app.constants import (
    PROVIDER_AGNES_IMAGE,
    PROVIDER_AGNES_VIDEO,
    PROVIDER_AI_HORDE,
    PROVIDER_COMFYUI,
    PROVIDER_WANGP,
)
from app.providers.agnes_image import DOC_URL as AGNES_IMAGE_DOC
from app.providers.agnes_image import MODEL as AGNES_IMAGE_MODEL
from app.providers.agnes_image import AgnesImageProvider
from app.providers.agnes_video import DOC_URL as AGNES_VIDEO_DOC
from app.providers.agnes_video import MODEL as AGNES_VIDEO_MODEL
from app.providers.agnes_video import AgnesVideoProvider
from app.providers.ai_horde import DOC_URL as HORDE_DOC
from app.providers.ai_horde import AIHordeProvider
from app.providers.base import BaseProvider
from app.providers.comfyui_future import ComfyuiProvider
from app.providers.wangp_future import WangpProvider


def build_providers(
    settings: Settings,
    client: Any,
    rate_limiter: Any,
    db: Any,
) -> dict[str, BaseProvider]:
    return {
        PROVIDER_AGNES_IMAGE: AgnesImageProvider(client, settings, rate_limiter, db),
        PROVIDER_AGNES_VIDEO: AgnesVideoProvider(client, settings, rate_limiter, db),
        PROVIDER_AI_HORDE: AIHordeProvider(client, settings, rate_limiter, db),
        PROVIDER_WANGP: WangpProvider(client, settings, rate_limiter, db),
        PROVIDER_COMFYUI: ComfyuiProvider(client, settings, rate_limiter, db),
    }


def seed_metadata(db: Any) -> None:
    """Seed provider pricing metadata used by the FREE-ONLY guard.

    Prices reflect the official documentation at build time. These values can
    change and are not guaranteed forever - hence the stale-pricing warning.
    """
    agnes_image_configured = "yes" if get_agnes_api_key() else "no"
    agnes_video_configured = "yes" if get_agnes_api_key() else "no"
    horde_configured = "yes" if get_horde_api_key() else "no"

    db.seed_provider_metadata(
        [
            {
                "provider": PROVIDER_AGNES_IMAGE,
                "model": AGNES_IMAGE_MODEL,
                "pricing_status": "free",
                "doc_label": AGNES_IMAGE_DOC,
                "enabled": True,
                "expected_price": "0 (docs: $0/image current, $0.003/image standard)",
                "notes": f"configured={agnes_image_configured}. "
                "Free pricing can change. Verify provider pricing before very "
                "large batches.",
            },
            {
                "provider": PROVIDER_AGNES_VIDEO,
                "model": AGNES_VIDEO_MODEL,
                "pricing_status": "free",
                "doc_label": AGNES_VIDEO_DOC,
                "enabled": True,
                "expected_price": "0 (docs: $0/sec current, $0.005/sec standard)",
                "notes": f"configured={agnes_video_configured}. "
                "Free pricing can change. Verify provider pricing before very "
                "large batches.",
            },
            {
                "provider": PROVIDER_AI_HORDE,
                "model": "dynamic (community models)",
                "pricing_status": "free",
                "doc_label": HORDE_DOC,
                "enabled": True,
                "expected_price": "0 (free/community provider)",
                "notes": f"configured={horde_configured}; anonymous mode supported. "
                "Anonymous requests have lowest queue priority.",
            },
            {
                "provider": PROVIDER_WANGP,
                "model": "future",
                "pricing_status": "unknown",
                "doc_label": "",
                "enabled": False,
                "expected_price": "n/a",
                "notes": "Not configured - intended for future local GPU hardware.",
            },
            {
                "provider": PROVIDER_COMFYUI,
                "model": "future",
                "pricing_status": "unknown",
                "doc_label": "",
                "enabled": False,
                "expected_price": "n/a",
                "notes": "Not configured - intended for future local GPU hardware.",
            },
        ]
    )
