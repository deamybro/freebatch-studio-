"""API routes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.config import BASE_DIR, get_agnes_api_key, get_horde_api_key
from app.constants import PROVIDER_NAMES
from app.database import iso_to_epoch
from app.main import app_state
from app.manifest import write_manifest
from app.parsers import parse_input
from app.pricing import (
    PricingError,
    check_free_only,
    large_batch_requires_ack,
    stale_warning_message,
)
from app.schemas import BatchCreateRequest, SettingsUpdate
from app.security import json_safe, mask_secret, validate_remote_url
from app.zipper import create_batch_zip

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# serialization helpers
# ---------------------------------------------------------------------------
def job_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "batch_id": row["batch_id"],
        "job_index": row["job_index"],
        "type": row["type"],
        "prompt": row["prompt"],
        "negative_prompt": row["negative_prompt"],
        "provider": row["provider"],
        "provider_name": PROVIDER_NAMES.get(row["provider"], row["provider"]),
        "model": row["model"],
        "fallback_provider": row["fallback_provider"],
        "requested_settings": _json_loads(row["requested_settings"]),
        "actual_settings": _json_loads(row["actual_settings"]),
        "status": row["status"],
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "remote_task_id": row["remote_task_id"],
        "remote_video_id": row["remote_video_id"],
        "remote_output_url": row["remote_output_url"],
        "local_output_path": row["local_output_path"],
        "error_type": row["error_type"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "updated_at": row["updated_at"],
        "generation_seconds": (
            round((iso_to_epoch(row["completed_at"]) or 0) - (iso_to_epoch(row["started_at"]) or 0), 1)
            if row["completed_at"] and row["started_at"] else None
        ),
    }


def batch_to_dict(row) -> dict:
    started = iso_to_epoch(row["created_at"])
    return {
        "id": row["id"],
        "name": row["name"],
        "media_type": row["media_type"],
        "status": row["status"],
        "total_jobs": row["total_jobs"],
        "completed_jobs": row["completed_jobs"],
        "failed_jobs": row["failed_jobs"],
        "pricing_acknowledged": bool(row["pricing_acknowledged"]),
        "fallback_provider": row["fallback_provider"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "elapsed_seconds": (
            round(iso_to_epoch(row["updated_at"]) - started, 1)
            if started and row["status"] in ("running", "paused") else None
        ),
    }


def _json_loads(value) -> dict:
    if not value:
        return {}
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# CSRF / meta
# ---------------------------------------------------------------------------
@router.get("/csrf")
async def csrf_token():
    return {"token": app_state.csrf.token()}


@router.get("/examples/{kind}.csv")
async def example_csv(kind: str):
    base = BASE_DIR / "examples"
    if kind == "image":
        path = base / "image_batch.csv"
    elif kind == "video":
        path = base / "video_batch.csv"
    else:
        raise HTTPException(404, "unknown example")
    if not path.exists():
        raise HTTPException(404, "example not found")
    return FileResponse(path, media_type="text/csv")


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------
@router.get("/dashboard")
async def dashboard():
    db = app_state.db
    counts = db.job_counts_all()
    batches_today = db._query_one(
        "SELECT COUNT(*) AS n FROM batches WHERE substr(created_at,1,10)=substr(date('now'),1,10)"
    )
    total_batches = db.count_batches()
    free_only = app_state.runtime.get_bool("free_only_mode", True)

    health = await _providers_health(force=False)
    online = sum(1 for h in health.values() if h and h.reachable is True)
    offline = sum(1 for h in health.values() if h and h.reachable is False)

    storage = _output_dir_usage_mb()
    return {
        "batches_today": int(batches_today["n"]) if batches_today else 0,
        "total_batches": total_batches,
        "jobs": counts,
        "free_only_mode": free_only,
        "providers_online": online,
        "providers_offline": offline,
        "providers": {
            name: h.to_dict() if h else None for name, h in health.items()
        },
        "storage_used_mb": round(storage, 2) if storage else None,
        "pricing_stale": bool(
            stale_warning_message(
                db, app_state.runtime.get_int("pricing_stale_days", 14)
            )
        ),
        "pricing_warning": stale_warning_message(
            db, app_state.runtime.get_int("pricing_stale_days", 14)
        ),
    }


def _output_dir_usage_mb() -> float:
    total = 0.0
    root = app_state.runtime.output_dir_path()
    if not root.exists():
        return 0.0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total / (1024 * 1024)


async def _providers_health(force: bool = False) -> dict:
    import time

    now = time.time()
    out: dict[str, object] = {}
    tasks = []
    names = []
    for name, provider in app_state.providers.items():
        cached = app_state.health_cache.get(name)
        if not force and cached and (now - cached[0]) < 30:
            out[name] = cached[1]
            continue
        tasks.append(provider.healthcheck())
        names.append(name)
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(names, results, strict=False):
            if isinstance(result, Exception):
                continue
            app_state.health_cache[name] = (time.time(), result)
            out[name] = result
    return out


# ---------------------------------------------------------------------------
# batch creation / parsing
# ---------------------------------------------------------------------------
@router.post("/batches/parse")
async def parse_batch(request: Request):
    body = await request.json()
    media_type = body.get("media_type")
    if media_type not in ("image", "video"):
        raise HTTPException(400, "media_type must be 'image' or 'video'")
    defaults = body.get("defaults") or {}
    jobs, errors = parse_input(
        media_type,
        body.get("text"),
        body.get("csv"),
        defaults,
    )
    return {"count": len(jobs), "jobs": jobs, "errors": errors}


@router.post("/batches", status_code=201)
async def create_batch(payload: BatchCreateRequest):
    if not payload.jobs:
        raise HTTPException(400, "no jobs to create")
    if len(payload.jobs) > 5000:
        raise HTTPException(400, "too many jobs (max 5000)")

    providers_used = {j.provider for j in payload.jobs}
    if payload.fallback_provider:
        providers_used.add(payload.fallback_provider)
    for provider in providers_used:
        try:
            check_free_only(
                app_state.db, provider, None,
                app_state.runtime.get_bool("free_only_mode", True),
            )
        except PricingError as exc:
            raise HTTPException(400, str(exc)) from exc

    large_threshold = app_state.runtime.get_int("large_batch_warn_threshold", 50)
    stale_days = app_state.runtime.get_int("pricing_stale_days", 14)
    if large_batch_requires_ack(
        app_state.db, len(payload.jobs), large_threshold, stale_days
    ) and not payload.pricing_acknowledged:
        raise HTTPException(
            400,
            stale_warning_message(app_state.db, stale_days)
            + " Acknowledge this warning to create a large batch.",
        )

    batch_id = app_state.db.create_batch(
        payload.name, payload.media_type, payload.fallback_provider,
        pricing_acknowledged=payload.pricing_acknowledged,
    )
    job_rows = [j.model_dump() for j in payload.jobs]
    for i, job in enumerate(job_rows):
        job["job_index"] = i
        job["max_attempts"] = job.pop("max_attempts", 3) or app_state.runtime.get_int(
            "max_attempts", 3
        )
    app_state.db.create_jobs(batch_id, job_rows)
    batch = app_state.db.get_batch(batch_id)
    return batch_to_dict(batch)


# ---------------------------------------------------------------------------
# batches
# ---------------------------------------------------------------------------
@router.get("/batches")
async def list_batches(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    rows = app_state.db.list_batches(limit=limit, offset=offset)
    return {
        "total": app_state.db.count_batches(),
        "batches": [batch_to_dict(r) for r in rows],
    }


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: int):
    batch = app_state.db.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "batch not found")
    return batch_to_dict(batch)


@router.get("/batches/{batch_id}/jobs")
async def list_jobs(
    batch_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None),
):
    batch = app_state.db.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "batch not found")
    if status and status not in ("pending", "queued_remote", "processing",
                                 "polling", "completed", "failed", "cancelled"):
        raise HTTPException(400, "invalid status filter")
    rows = app_state.db.list_jobs(batch_id, limit=limit, offset=offset, status=status)
    return {
        "batch": batch_to_dict(batch),
        "total": app_state.db.count_jobs(batch_id),
        "jobs": [job_to_dict(r) for r in rows],
        "counts": {
            s: app_state.db.count_jobs_by_status(batch_id, s)
            for s in ("pending", "queued_remote", "processing", "polling",
                      "completed", "failed", "cancelled")
        },
    }


@router.post("/batches/{batch_id}/pause")
async def pause_batch(batch_id: int):
    _require_batch(batch_id)
    app_state.queue.pause_batch(batch_id)
    return {"ok": True}


@router.post("/batches/{batch_id}/resume")
async def resume_batch(batch_id: int):
    _require_batch(batch_id)
    app_state.queue.resume_batch(batch_id)
    return {"ok": True}


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: int):
    _require_batch(batch_id)
    app_state.queue.cancel_batch(batch_id)
    return {"ok": True}


@router.post("/batches/{batch_id}/retry-failed")
async def retry_failed(batch_id: int):
    _require_batch(batch_id)
    n = app_state.queue.retry_failed_batch(batch_id)
    return {"ok": True, "requeued": n}


@router.post("/batches/{batch_id}/duplicate")
async def duplicate_batch(batch_id: int):
    _require_batch(batch_id)
    new_id = app_state.db.duplicate_batch(batch_id)
    return {"ok": True, "new_batch_id": new_id}


@router.get("/batches/{batch_id}/manifest")
async def export_manifest(batch_id: int):
    _require_batch(batch_id)
    _, json_path = write_manifest(
        app_state.db, batch_id, out_root=app_state.runtime.output_dir_path()
    )
    return FileResponse(
        json_path, media_type="application/json",
        filename=f"batch_{batch_id}_manifest.json",
    )


@router.get("/batches/{batch_id}/manifest.csv")
async def export_manifest_csv(batch_id: int):
    _require_batch(batch_id)
    csv_path, _ = write_manifest(
        app_state.db, batch_id, out_root=app_state.runtime.output_dir_path()
    )
    return FileResponse(
        csv_path, media_type="text/csv",
        filename=f"batch_{batch_id}_manifest.csv",
    )


@router.get("/batches/{batch_id}/zip")
async def export_zip(batch_id: int):
    _require_batch(batch_id)
    out_root = app_state.runtime.output_dir_path()
    completed = {
        Path(r["local_output_path"]).name
        for r in app_state.db.list_jobs(batch_id, limit=100000)
        if r["status"] == "completed" and r["local_output_path"]
    }
    try:
        zip_path = create_batch_zip(
            batch_id,
            include_failed=app_state.runtime.get_bool("zip_include_failed", False),
            include_metadata=app_state.runtime.get_bool("zip_include_metadata", True),
            out_root=out_root,
            completed_files=completed,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(
        zip_path, media_type="application/zip",
        filename=f"batch_{batch_id}.zip",
    )


def _require_batch(batch_id: int):
    if not app_state.db.get_batch(batch_id):
        raise HTTPException(404, "batch not found")


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------
@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: int):
    job = app_state.db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    app_state.queue.retry_job(job_id)
    return {"ok": True}


@router.get("/jobs/{job_id}/file")
async def job_file(job_id: int):
    job = app_state.db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if not job["local_output_path"]:
        raise HTTPException(404, "job has no local output")
    path = _resolve_output(job["local_output_path"])
    if not path.is_file():
        raise HTTPException(404, "output file missing")
    mime = "video/mp4" if path.suffix.lower() == ".mp4" else "image/png"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    if path.suffix.lower() == ".webp":
        mime = "image/webp"
    return FileResponse(path, media_type=mime, filename=path.name)


def _resolve_output(relative_or_absolute: str) -> Path:
    root = app_state.runtime.output_dir_path().resolve()
    candidate = Path(relative_or_absolute)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise HTTPException(400, "output path outside output directory")
    return resolved


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: int, limit: int = Query(100, le=500)):
    rows = app_state.db.events_for_job(job_id, limit=limit)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------
@router.get("/providers")
async def list_providers():
    meta_rows = app_state.db.provider_metadata()
    out = []
    for name, provider in app_state.providers.items():
        metas = [dict(m) for m in meta_rows if m["provider"] == name]
        info = {
            "name": name,
            "display_name": provider.display_name,
            "media_types": list(provider.media_types),
            "enabled": _provider_enabled(name, metas),
            "configured": provider.is_configured(),
            "capabilities": json_safe(provider.capabilities()),
            "pricing": metas,
            "secrets": {
                "agn_configured": _masked_status(name),
            },
        }
        cached = app_state.health_cache.get(name)
        if cached:
            info["health"] = cached[1].to_dict()
        out.append(info)
    return {
        "free_only_mode": app_state.runtime.get_bool("free_only_mode", True),
        "stale_days": app_state.runtime.get_int("pricing_stale_days", 14),
        "pricing_warning": stale_warning_message(
            app_state.db, app_state.runtime.get_int("pricing_stale_days", 14)
        ),
        "providers": out,
    }


def _provider_enabled(name: str, metas: list[dict]) -> bool:
    if name in ("wangp", "comfyui"):
        return False
    return bool(metas and metas[0].get("enabled"))


def _masked_status(name: str) -> str:
    if name == "ai_horde":
        key = get_horde_api_key()
        if not key or key == "0000000000":
            return "anonymous mode"
        return f"Configured: {mask_secret(key)}"
    key = get_agnes_api_key()
    if not key:
        return "(not configured)"
    return f"Configured: {mask_secret(key)}"


@router.post("/providers/{name}/health")
async def provider_health(name: str):
    provider = app_state.providers.get(name)
    if not provider:
        raise HTTPException(404, "unknown provider")
    health = await provider.healthcheck()
    import time

    app_state.health_cache[name] = (time.time(), health)
    return health.to_dict()


@router.post("/providers/{name}/verify")
async def provider_verify(name: str):
    provider = app_state.providers.get(name)
    if not provider:
        raise HTTPException(404, "unknown provider")
    if name in ("wangp", "comfyui"):
        raise HTTPException(400, "future provider not configured")
    free_only = app_state.runtime.get_bool("free_only_mode", True)
    try:
        check_free_only(app_state.db, name, None, free_only)
    except PricingError as exc:
        raise HTTPException(400, str(exc)) from exc

    verify = getattr(provider, "verify", None)
    if not verify:
        raise HTTPException(400, "no verification available for this provider")
    health = await verify()
    import time

    app_state.health_cache[name] = (time.time(), health)
    if health.authenticated is True:
        for meta in app_state.db.provider_metadata():
            if meta["provider"] == name:
                app_state.db.touch_provider_verified(name, meta["model"])
    return health.to_dict()


@router.get("/providers/{name}/models")
async def provider_models(name: str):
    provider = app_state.providers.get(name)
    if not provider:
        raise HTTPException(404, "unknown provider")
    fetch = getattr(provider, "active_models", None)
    if not fetch:
        raise HTTPException(400, "provider has no dynamic model list")
    try:
        models = await fetch(force=True)
    except Exception as exc:
        raise HTTPException(502, f"could not fetch models: {exc}") from exc
    return {
        "models": [
            {
                "name": m.get("name"),
                "workers": m.get("count", 0),
                "queued": m.get("queued", 0),
                "eta": m.get("eta"),
            }
            for m in models
        ]
    }


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
SETTINGS_KEYS = {
    "output_dir", "poll_interval_seconds", "max_attempts",
    "request_timeout_seconds", "download_timeout_seconds",
    "max_download_size_mb", "max_reference_size_mb", "pricing_stale_days",
    "large_batch_warn_threshold", "free_only_mode", "horde_anonymous",
    "horde_steps", "horde_max_dimension", "zip_include_failed",
    "zip_include_metadata", "log_level", "rate_limits",
}


@router.get("/settings")
async def get_settings_api():
    data = app_state.runtime.public_dict()
    data["secret_status"] = {
        "AGNES_API_KEY": _masked_status("agnes_image"),
        "AI_HORDE_API_KEY": _masked_status("ai_horde"),
    }
    return data


@router.put("/settings")
async def update_settings(payload: SettingsUpdate):
    invalid = [k for k in payload.values if k not in SETTINGS_KEYS]
    if invalid:
        raise HTTPException(400, f"unknown settings keys: {invalid}")
    for key, value in payload.values.items():
        if key == "output_dir":
            value = str(value).strip().rstrip("/\\") or "data/outputs"
        if key == "free_only_mode":
            value = str(value).strip().lower() in ("1", "true", "yes", "on")
    app_state.runtime.set_many(payload.values)
    app_state.reload_rate_limits()
    if "log_level" in payload.values:
        import logging

        logging.getLogger("freebatch").setLevel(
            getattr(logging, str(payload.values["log_level"]).upper(), logging.INFO)
        )
    return app_state.runtime.public_dict()


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
@router.get("/logs")
async def get_logs(limit: int = Query(300, ge=1, le=2000)):
    log_path = app_state.runtime.settings.logs_dir_path() / "app.log"
    if not log_path.exists():
        return {"lines": []}
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    lines = [line.rstrip("\n") for line in lines[-limit:]]
    return {"lines": lines}


# ---------------------------------------------------------------------------
# image upload → public URL (for image-to-video / keyframes)
# ---------------------------------------------------------------------------
@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):  # noqa: B008
    import uuid

    import httpx

    from app.downloader import sniff_mime

    # Basic validation
    if not file.content_type or not file.content_type.startswith("image/"):
        # also allow by extension fallback
        ext = (file.filename or "").lower().rsplit(".", 1)[-1] if "." in (file.filename or "") else ""
        if ext not in ("jpg", "jpeg", "png", "webp", "gif", "bmp"):
            raise HTTPException(400, "only image files are allowed (jpg, png, webp, gif, bmp)")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    max_bytes = 10 * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(400, "file too large (max 10MB)")
    # MIME sniff to reject non-images
    mime = sniff_mime(data[:1024])
    if mime and not mime.startswith("image/"):
        raise HTTPException(400, f"invalid image type: {mime}")

    # Try catbox.moe (no auth, public) — most reliable free host
    fname = file.filename or f"upload_{uuid.uuid4().hex[:8]}.png"
    # sanitize extension
    if "." not in fname:
        fname += ".png"

    async def _try_fileio() -> str | None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as client:
                files = {"file": (fname, data, file.content_type or "image/png")}
                resp = await client.post("https://file.io", files=files)
                if resp.status_code == 200:
                    j = resp.json()
                    url = j.get("link") or j.get("url")
                    if url and url.startswith("http"):
                        return url
        except Exception:
            pass
        return None

    async def _try_catbox() -> str | None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
                files = {"fileToUpload": (fname, data, file.content_type or "image/png")}
                resp = await client.post(
                    "https://catbox.moe/user/api.php",
                    data={"reqtype": "fileupload"},
                    files=files,
                )
                if resp.status_code == 200:
                    url = resp.text.strip()
                    if url.startswith("http"):
                        return url
        except Exception:
            pass
        return None

    async def _try_tmpfiles() -> str | None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as client:
                files = {"file": (fname, data, file.content_type or "image/png")}
                resp = await client.post("https://tmpfiles.org/api/v1/upload", files=files)
                if resp.status_code == 200:
                    j = resp.json()
                    # tmpfiles returns {"data": {"url": "https://tmpfiles.org/abc/file.png"}}
                    data_field = j.get("data")
                    url = data_field.get("url") if isinstance(data_field, dict) else j.get("url")
                    if url and url.startswith("http"):
                        # convert page URL to direct download URL
                        if "tmpfiles.org/" in url and "/dl/" not in url:
                            url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
                        return url
        except Exception:
            pass
        return None

    # file.io is fastest in this environment (catbox timed out in tests)
    public_url = await _try_fileio()
    if not public_url:
        public_url = await _try_catbox()
    if not public_url:
        public_url = await _try_tmpfiles()

    if public_url:
        # Validate it is a public HTTPS URL
        from contextlib import suppress

        with suppress(Exception):
            public_url = validate_remote_url(public_url)
        return {"ok": True, "url": public_url, "filename": fname, "size": len(data)}

    # Fallback: save locally and return a local URL that the user can still
    # preview, but warn that Agnes requires a public URL — suggest retry.
    raise HTTPException(
        502,
        "could not upload to public host (catbox.moe / file.io). Please try again or use a direct https:// image URL.",
    )


# ---------------------------------------------------------------------------
# reference URL validation helper (used by the UI for image-to-video)
# ---------------------------------------------------------------------------
@router.post("/validate-url")
async def validate_url(request: Request):
    body = await request.json()
    url = (body.get("url") or "").strip()
    try:
        normalized = validate_remote_url(url)
        return {"ok": True, "url": normalized}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
