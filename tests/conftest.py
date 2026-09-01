"""Shared test fixtures.

Note: importing ``app.main`` builds the real :class:`AppState` at import time
using :data:`app.config.get_settings` (which reads ``.env``). To keep tests
hermetic we avoid importing ``app.main`` here and instead construct a
:class:`Settings` with temporary paths directly.
"""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest
from app.config import Settings
from app.database import Database

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="freebatch-tests-"))


def make_settings(**overrides) -> Settings:
    base = {
        "data_dir": str(TEST_DATA_DIR / "data"),
        "output_dir": str(TEST_DATA_DIR / "outputs"),
        "logs_dir": str(TEST_DATA_DIR / "logs"),
        "db_path": str(TEST_DATA_DIR / "test.db"),
        "log_level": "CRITICAL",
        "free_only_mode": True,
        "pricing_stale_days": 14,
        "large_batch_warn_threshold": 50,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def db(settings: Settings) -> Database:
    database = Database(settings.db_path_resolved())
    yield database
    database.db_path.unlink(missing_ok=True)
    for part in Path(settings.db_path_resolved().parent).glob("*.db-*"):
        part.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Never let real provider keys leak into test runs."""
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("AI_HORDE_API_KEY", raising=False)
    monkeypatch.delenv("FREE_ONLY_MODE", raising=False)
    yield


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    """Stub DNS resolution so the SSRF guard works without network.

    ``validate_remote_url`` resolves hostnames via ``socket.getaddrinfo``.
    Placeholder test hosts never resolve on the developer machine, so we
    substitute a public address. Private-host rejection is tested separately
    at the IP/hostname level (before DNS) in ``test_security.py``.
    """

    def _fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("app.security.socket.getaddrinfo", _fake_getaddrinfo)
    yield


@pytest.fixture
def client(settings: Settings):
    """An httpx.AsyncClient bound to no real network (tests use respx)."""
    import httpx

    return httpx.AsyncClient(
        timeout=httpx.Timeout(10),
        transport=httpx.MockTransport(handler=lambda req: _unhandled(req)),
    )


def _unhandled(request):
    import respx

    raise respx.RouterError(f"unmocked request: {request.method} {request.url}")


@pytest.fixture
def rate_limiter():
    from app.rate_limiter import RateLimiter

    rl = RateLimiter()
    rl.load_rates(None, {"agnes_image:1k": 1000, "default": 1000})
    return rl


@pytest.fixture
def runtime(settings: Settings, db: Database):
    from app.runtime import RuntimeConfig

    rt = RuntimeConfig(db, settings)
    rt.load()
    return rt


@pytest.fixture
def providers(settings: Settings, client, rate_limiter, db):
    from app.providers.registry import build_providers

    return build_providers(settings, client, rate_limiter, db)


def seed_pricing(db: Database) -> None:
    """Seed the FREE-ONLY pricing rows the guard requires."""
    db.seed_provider_metadata(
        [
            {
                "provider": "agnes_image",
                "model": "agnes-image-2.1-flash",
                "pricing_status": "free",
                "enabled": True,
                "expected_price": "0",
                "notes": "test",
            },
            {
                "provider": "agnes_video",
                "model": "agnes-video-v2.0",
                "pricing_status": "free",
                "enabled": True,
                "expected_price": "0",
                "notes": "test",
            },
            {
                "provider": "ai_horde",
                "model": "community",
                "pricing_status": "free",
                "enabled": True,
                "expected_price": "0",
                "notes": "test",
            },
        ]
    )
