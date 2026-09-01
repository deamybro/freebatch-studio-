"""TXT and CSV batch parsers.

Both parsers are permissive: they return the parsed jobs plus a list of
row-level errors so the UI can preview problems before a batch is created.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from app.constants import (
    AGNES_IMAGE_RATIOS,
    AGNES_IMAGE_TIERS,
    DURATION_PRESETS,
    HORDE_RATIO_DIMENSIONS,
)
from app.security import safe_slug

IMAGE_COLUMNS = [
    "prompt", "type", "provider", "model", "size", "ratio",
    "negative_prompt", "image_url", "seed",
]
VIDEO_COLUMNS = [
    "prompt", "type", "provider", "model", "image_url",
    "negative_prompt", "seed", "duration", "fps", "width", "height",
    "keyframe_urls",
]


def parse_txt(text: str) -> list[str]:
    """One non-empty trimmed line = one prompt."""
    prompts: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            prompts.append(line)
    return prompts


def _strip_bom(text: str) -> str:
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _coerce_int(value: str | None, field: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"'{field}' must be an integer, got '{value}'") from exc


def _coerce_bool(value: str | None) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _normalize_type(raw: str | None, media_type: str) -> str:
    mapping = {
        "text_to_image": "text_to_image",
        "t2i": "text_to_image",
        "txt2img": "text_to_image",
        "image_to_image": "image_to_image",
        "i2i": "image_to_image",
        "img2img": "image_to_image",
        "multi_image": "multi_image",
        "multi": "multi_image",
        "text_to_video": "text_to_video",
        "t2v": "text_to_video",
        "txt2video": "text_to_video",
        "image_to_video": "image_to_video",
        "i2v": "image_to_video",
        "img2video": "image_to_video",
        "keyframes": "keyframes",
        "keyframe": "keyframes",
    }
    if not raw or not str(raw).strip():
        return "text_to_image" if media_type == "image" else "text_to_video"
    key = str(raw).strip().lower().replace("-", "_")
    return mapping.get(key, key)


def _split_image_urls(value: str | None) -> list[str]:
    if not value or not str(value).strip():
        return []
    return [u.strip() for u in str(value).split("|") if u.strip()]


def _normalize_frames(raw_frames: int | None) -> int:
    if raw_frames is None:
        return 121
    raw_frames = max(1, min(441, int(raw_frames)))
    # 8n + 1 rule: snap to nearest allowed value
    n = round((raw_frames - 1) / 8)
    n = max(0, min(55, n))
    return 8 * n + 1


def parse_csv(
    text: str, media_type: str, defaults: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse flexible CSV into job dicts.

    Returns (jobs, errors). Blank rows and duplicate prompts are skipped
    (duplicates are still counted once). Missing optional columns fall back to
    defaults.
    """
    text = _strip_bom(text or "")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], ["CSV has no header row"]

    fields = [f.strip().lower() for f in reader.fieldnames]
    if "prompt" not in fields:
        return [], ["CSV is missing a 'prompt' column"]

    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    index = 0

    for row_no, raw in enumerate(reader, start=2):
        row = {f.strip().lower(): (raw.get(f) or "").strip() for f in reader.fieldnames}
        prompt = row.get("prompt", "").strip()
        if not prompt:
            continue
        if prompt in seen:
            errors.append(f"row {row_no}: duplicate prompt skipped")
            continue
        seen.add(prompt)

        job_type = _normalize_type(row.get("type"), media_type)
        settings = dict(defaults or {})
        errors_for_row: list[str] = []

        if media_type == "image":
            size = (row.get("size") or settings.get("size") or "1K").upper()
            ratio = (row.get("ratio") or settings.get("ratio") or "1:1")
            if size not in AGNES_IMAGE_TIERS:
                errors_for_row.append(f"row {row_no}: unsupported size '{size}', using 1K")
                size = "1K"
            if ratio not in AGNES_IMAGE_RATIOS:
                errors_for_row.append(
                    f"row {row_no}: unsupported ratio '{ratio}', using 1:1"
                )
                ratio = "1:1"
            settings["size"] = size
            settings["ratio"] = ratio
            if row.get("negative_prompt"):
                settings["negative_prompt"] = row["negative_prompt"]
            images = _split_image_urls(row.get("image_url"))
            if job_type in ("image_to_image", "multi_image") and images:
                settings["image_urls"] = images
            if job_type == "text_to_image" and images:
                job_type = "image_to_image"
                settings["image_urls"] = images
            if job_type == "image_to_image" and len(images) > 1:
                job_type = "multi_image"
            if job_type in ("image_to_image", "multi_image") and not images:
                errors_for_row.append(
                    f"row {row_no}: {job_type} requires at least one image_url"
                )
        else:
            duration = (row.get("duration") or settings.get("duration") or "5s")
            if duration not in DURATION_PRESETS:
                errors_for_row.append(
                    f"row {row_no}: unsupported duration '{duration}', using 5s"
                )
                duration = "5s"
            settings["duration"] = duration
            settings["num_frames"] = DURATION_PRESETS[duration]

            fps = _coerce_int(row.get("fps"), "fps")
            if fps is None:
                fps = int(settings.get("fps") or 24)
            fps = max(1, min(60, fps))
            settings["fps"] = fps

            width = _coerce_int(row.get("width"), "width")
            height = _coerce_int(row.get("height"), "height")
            if width and height:
                settings["width"] = width
                settings["height"] = height
            else:
                settings.pop("width", None)
                settings.pop("height", None)

            if row.get("negative_prompt"):
                settings["negative_prompt"] = row["negative_prompt"]
            image_url = (row.get("image_url") or "").strip()
            keyframes = _split_image_urls(row.get("keyframe_urls"))
            if job_type == "keyframes":
                settings["keyframe_urls"] = keyframes
                if not keyframes:
                    errors_for_row.append(
                        f"row {row_no}: keyframes requires keyframe_urls"
                    )
            elif job_type == "image_to_video":
                if not image_url:
                    errors_for_row.append(
                        f"row {row_no}: image_to_video requires a public image_url"
                    )
                settings["image_url"] = image_url
            elif image_url and job_type == "text_to_video":
                job_type = "image_to_video"
                settings["image_url"] = image_url

        seed = _coerce_int(row.get("seed"), "seed")
        if seed is not None:
            settings["seed"] = seed

        provider = (row.get("provider") or settings.get("provider") or
                    ("agnes_image" if media_type == "image" else "agnes_video"))
        model = (row.get("model") or settings.get("model") or None)
        if provider == "ai_horde":
            settings.pop("ratio", None)
            ratio = (row.get("ratio") or defaults.get("ratio") or "1:1")
            if ratio in HORDE_RATIO_DIMENSIONS:
                settings["width"], settings["height"] = HORDE_RATIO_DIMENSIONS[ratio]
                settings.pop("size", None)

        jobs.append(
            {
                "job_index": index,
                "type": job_type,
                "prompt": prompt,
                "negative_prompt": row.get("negative_prompt") or None,
                "provider": provider,
                "model": model,
                "requested_settings": settings,
                "seed": seed,
            }
        )
        errors.extend(errors_for_row)
        index += 1

    return jobs, errors


def parse_input(
    media_type: str,
    text: str | None,
    csv_text: str | None,
    defaults: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse either pasted text (one prompt per line) or CSV content."""
    defaults = defaults or {}
    if csv_text and csv_text.strip():
        return parse_csv(csv_text, media_type, defaults)
    prompts = parse_txt(text or "")
    jobs: list[dict[str, Any]] = []
    for i, prompt in enumerate(prompts):
        settings = dict(defaults or {})
        # respect explicit kind/type from UI (e.g. image_to_video, keyframes)
        raw_kind = defaults.get("kind") or defaults.get("type")
        job_type = _normalize_type(raw_kind, media_type)
        # keep image_url / keyframe_urls handling consistent with CSV path
        if media_type == "video":
            if settings.get("image_url") and job_type in ("text_to_video", "image_to_video"):
                job_type = "image_to_video"
            if job_type == "keyframes" and settings.get("keyframe_urls"):
                job_type = "keyframes"
        else:
            # image batch: respect image_urls
            if settings.get("image_urls"):
                urls = settings.get("image_urls")
                if isinstance(urls, list) and len(urls) > 1:
                    job_type = "multi_image"
                elif urls:
                    job_type = "image_to_image"
        jobs.append(
            {
                "job_index": i,
                "type": job_type,
                "prompt": prompt,
                "negative_prompt": defaults.get("negative_prompt"),
                "provider": defaults.get("provider")
                or ("agnes_image" if media_type == "image" else "agnes_video"),
                "model": defaults.get("model"),
                "requested_settings": settings,
                "seed": defaults.get("seed"),
            }
        )
    return jobs, []


def job_slug(prompt: str) -> str:
    return safe_slug(prompt)
