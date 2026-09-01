"""Page routes (HTML)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.main import TEMPLATES, app_state

router = APIRouter()


def _ctx(request: Request, **extra):
    from app.pricing import stale_warning_message

    settings = get_settings()
    ctx = {
        "request": request,
        "app_name": settings.app_name,
        "version": settings.version,
        "free_only_mode": app_state.runtime.get_bool("free_only_mode", True),
        "csrf_token": app_state.csrf.token(),
        "stale_warning": stale_warning_message(
            app_state.db, app_state.runtime.get_int("pricing_stale_days", 14)
        ),
    }
    ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return TEMPLATES.TemplateResponse(request, "dashboard.html", _ctx(request))


@router.get("/batches/new-image", response_class=HTMLResponse)
async def new_image_batch(request: Request):
    return TEMPLATES.TemplateResponse(
        request, "new_image_batch.html", _ctx(request)
    )


@router.get("/batches/new-video", response_class=HTMLResponse)
async def new_video_batch(request: Request):
    return TEMPLATES.TemplateResponse(
        request, "new_video_batch.html", _ctx(request)
    )


@router.get("/batches", response_class=HTMLResponse)
async def batches(request: Request):
    return TEMPLATES.TemplateResponse(request, "batches.html", _ctx(request))


@router.get("/batches/{batch_id}", response_class=HTMLResponse)
async def batch_detail(request: Request, batch_id: int):
    batch = app_state.db.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch not found")
    return TEMPLATES.TemplateResponse(
        request, "batch_detail.html",
        _ctx(request, batch_id=batch_id, batch=batch),
    )


@router.get("/providers", response_class=HTMLResponse)
async def providers(request: Request):
    return TEMPLATES.TemplateResponse(request, "providers.html", _ctx(request))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "settings.html", _ctx(request))


@router.get("/logs", response_class=HTMLResponse)
async def logs(request: Request):
    return TEMPLATES.TemplateResponse(request, "logs.html", _ctx(request))
