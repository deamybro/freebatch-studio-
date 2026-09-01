"""WanGP - disabled future-provider slot.

Intentionally NOT installed and NOT functional until explicitly configured on
future local GPU hardware. No models are downloaded.
"""

from __future__ import annotations

from app.constants import PROVIDER_WANGP
from app.providers.base import DisabledProvider

FUTURE_REASON = "Not configured"


class WangpProvider(DisabledProvider):
    name = PROVIDER_WANGP
    display_name = "WanGP"
    media_types = ("image", "video")
    reason = FUTURE_REASON

    def capabilities(self) -> dict[str, dict[str, object]]:
        return {
            "enabled": False,
            "reason": FUTURE_REASON
            + " - intended for future local GPU hardware.",
            "note": "WanGP requires a local GPU and model weights; neither are "
            "installed by FreeBatch Studio.",
        }
