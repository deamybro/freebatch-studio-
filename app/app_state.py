"""Application state container - wires DB, runtime, limiter, providers, queue."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.database import Database
from app.logging_setup import setup_logging
from app.providers.registry import build_providers, seed_metadata
from app.queue_manager import QueueManager
from app.rate_limiter import RateLimiter
from app.runtime import RuntimeConfig
from app.security import CsrfProtector

logger = logging.getLogger("freebatch")


class AppState:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        setup_logging(str(self.settings.logs_dir_path()), self.settings.log_level)
        self.db = Database(self.settings.db_path_resolved())
        self.runtime = RuntimeConfig(self.db, self.settings)
        self.runtime.load()
        self.rate_limiter = RateLimiter()
        self.rate_limiter.load_rates(
            self.runtime.get("rate_limits", None),
            dict(self.settings.DEFAULT_RATE_LIMITS),
        )
        self.client: httpx.AsyncClient | None = None
        self.providers: dict[str, Any] = {}
        self.queue: QueueManager | None = None
        self.csrf = CsrfProtector(self.db)
        self.health_cache: dict[str, tuple[float, Any]] = {}
        self._started = False

    # -- lifecycle --------------------------------------------------------
    def startup(self) -> None:
        seed_metadata(self.db)
        self.csrf.check_env_setup()
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.request_timeout_seconds),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=2),
        )
        self.providers = build_providers(
            self.settings, self.client, self.rate_limiter, self.db,
        )
        self.queue = QueueManager(
            self.db, self.runtime, self.rate_limiter, self.providers, self.client,
        )
        self.queue.reconcile_on_startup()
        self.queue.start()
        self._started = True
        logger.info("FreeBatch Studio started (free_only=%s)",
                    self.runtime.get_bool("free_only_mode", True))

    async def shutdown(self) -> None:
        if self.queue:
            await self.queue.stop()
        if self.client:
            await self.client.aclose()
        self._started = False
        logger.info("FreeBatch Studio stopped")

    def reload_rate_limits(self) -> None:
        self.rate_limiter.load_rates(
            self.runtime.get("rate_limits", None),
            dict(self.settings.DEFAULT_RATE_LIMITS),
        )


def create_app_state(settings: Settings | None = None) -> AppState:
    return AppState(settings)
