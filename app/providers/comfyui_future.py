"""ComfyUI - disabled future-provider slot.

Intentionally NOT installed and NOT functional until a local ComfyUI server is
explicitly configured. No models are downloaded.
"""

from __future__ import annotations

from app.constants import PROVIDER_COMFYUI
from app.providers.base import DisabledProvider

FUTURE_REASON = "Not configured"


class ComfyuiProvider(DisabledProvider):
    name = PROVIDER_COMFYUI
    display_name = "ComfyUI"
    media_types = ("image", "video")
    reason = FUTURE_REASON

    def capabilities(self) -> dict[str, dict[str, object]]:
        return {
            "enabled": False,
            "reason": FUTURE_REASON
            + " - intended for future local GPU hardware.",
            "note": "ComfyUI support requires a locally running ComfyUI server "
            "with checkpoints; none are bundled.",
        }
