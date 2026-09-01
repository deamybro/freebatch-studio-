"""Shared domain constants and enums."""

from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# Job / batch states
# ---------------------------------------------------------------------------
class JobType(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    MULTI_IMAGE = "multi_image"
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    KEYFRAMES = "keyframes"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


JOB_STATES = (
    "draft",
    "pending",
    "queued_remote",
    "processing",
    "polling",
    "completed",
    "failed",
    "paused",
    "cancelled",
)

BATCH_STATES = ("running", "paused", "completed", "cancelled")

TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
PROVIDER_AGNES_IMAGE = "agnes_image"
PROVIDER_AGNES_VIDEO = "agnes_video"
PROVIDER_AI_HORDE = "ai_horde"
PROVIDER_WANGP = "wangp"
PROVIDER_COMFYUI = "comfyui"

PROVIDER_NAMES = {
    PROVIDER_AGNES_IMAGE: "Agnes Image",
    PROVIDER_AGNES_VIDEO: "Agnes Video",
    PROVIDER_AI_HORDE: "AI Horde",
    PROVIDER_WANGP: "WanGP",
    PROVIDER_COMFYUI: "ComfyUI",
}

# ---------------------------------------------------------------------------
# Agnes Image tiers / ratios
# ---------------------------------------------------------------------------
AGNES_IMAGE_TIERS = ("1K", "2K", "3K", "4K")
AGNES_IMAGE_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9")

AGNES_IMAGE_DIMENSIONS = {
    # (ratio, tier) -> "WxH"
    ("1:1", "1K"): "1024x1024",
    ("1:1", "2K"): "2048x2048",
    ("1:1", "3K"): "3072x3072",
    ("1:1", "4K"): "4096x4096",
    ("3:4", "1K"): "864x1152",
    ("3:4", "2K"): "1728x2304",
    ("3:4", "3K"): "2592x3456",
    ("3:4", "4K"): "3456x4608",
    ("4:3", "1K"): "1152x864",
    ("4:3", "2K"): "2304x1728",
    ("4:3", "3K"): "3456x2592",
    ("4:3", "4K"): "4608x3456",
    ("16:9", "1K"): "1312x736",
    ("16:9", "2K"): "2624x1472",
    ("16:9", "3K"): "3936x2208",
    ("16:9", "4K"): "5248x2944",
    ("9:16", "1K"): "736x1312",
    ("9:16", "2K"): "1472x2624",
    ("9:16", "3K"): "2208x3936",
    ("9:16", "4K"): "2944x5248",
    ("2:3", "1K"): "832x1248",
    ("2:3", "2K"): "1664x2496",
    ("2:3", "3K"): "2496x3744",
    ("2:3", "4K"): "3328x4992",
    ("3:2", "1K"): "1248x832",
    ("3:2", "2K"): "2496x1664",
    ("3:2", "3K"): "3744x2496",
    ("3:2", "4K"): "4992x3328",
    ("21:9", "1K"): "1568x672",
    ("21:9", "2K"): "3136x1344",
    ("21:9", "3K"): "4704x2016",
    ("21:9", "4K"): "6272x2688",
}

# ---------------------------------------------------------------------------
# Agnes Video presets (num_frames, fps) - 8n+1 rule, max 441
# ---------------------------------------------------------------------------
AGNES_VIDEO_FRAME_PRESETS = (81, 121, 161, 201, 241, 281, 321, 361, 401, 441)

DURATION_PRESETS = {
    "3s": 81,
    "5s": 121,
    "10s": 241,
    "18s": 441,
}

VIDEO_RATIO_DIMENSIONS = {
    # (ratio, tier) -> (width, height)  ... tiers 480p/720p/1080p
    ("16:9", "480p"): (832, 448),
    ("16:9", "720p"): (1152, 640),
    ("16:9", "1080p"): (1728, 960),
    ("9:16", "480p"): (448, 832),
    ("9:16", "720p"): (640, 1152),
    ("9:16", "1080p"): (960, 1728),
    ("1:1", "480p"): (672, 672),
    ("1:1", "720p"): (896, 896),
    ("1:1", "1080p"): (1344, 1344),
    ("4:3", "480p"): (768, 576),
    ("4:3", "720p"): (1024, 768),
    ("4:3", "1080p"): (1536, 1152),
    ("3:4", "480p"): (576, 768),
    ("3:4", "720p"): (768, 1024),
    ("3:4", "1080p"): (1152, 1536),
}

# ---------------------------------------------------------------------------
# AI Horde dimension mapping (multiples of 64, capped at anonymous max)
# ---------------------------------------------------------------------------
HORDE_RATIO_DIMENSIONS = {
    "1:1": (1024, 1024),
    "3:4": (768, 1024),
    "4:3": (1024, 768),
    "16:9": (1024, 576),
    "9:16": (576, 1024),
    "2:3": (640, 960),
    "3:2": (960, 640),
    "21:9": (896, 384),
}

HORDE_ANONYMOUS_KEY = "0000000000"
