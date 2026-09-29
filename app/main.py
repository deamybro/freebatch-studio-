"""FreeBatch Studio - FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.app_state import AppState
from app.config import BASE_DIR, get_settings
from app.security import SecurityError

logger = logging.getLogger("freebatch")

settings = get_settings()
app_state = AppState(settings)

TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
STATIC_DIR = BASE_DIR / "app" / "static"

# Routes import ``app_state`` from this module, so they must be imported only
# after ``app_state`` has been constructed (avoids a circular-import failure).
from app.routes import api as api_routes  # noqa: E402
from app.routes import pages as page_routes  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_state.startup()
    yield
    await app_state.shutdown()


app = FastAPI(
    title="FreeBatch Studio",
    version=settings.version,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.url.path.startswith("/api/")
    ):
        token = request.headers.get("x-csrf-token")
        try:
            app_state.csrf.validate(token)
        except SecurityError as exc:
            return JSONResponse(
                status_code=403,
                content={"detail": f"CSRF check failed: {exc}"},
            )
    return await call_next(request)


@app.exception_handler(SecurityError)
async def security_error_handler(request: Request, exc: SecurityError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(page_routes.router)
app.include_router(api_routes.router)


def run(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=host or settings.host,
        port=port or settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    run()
