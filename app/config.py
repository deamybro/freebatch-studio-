"""FreeBatch Studio - application configuration.

API keys are read from settings, which loads them from the `.env` file and/or
the process environment (AGNES_API_KEY / AI_HORDE_API_KEY). They are never
stored in SQLite or returned to the frontend.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FreeBatch Studio"
    version: str = "1.0.0"
    host: str = "127.0.0.1"
    port: int = 8737

    free_only_mode: bool = True

    data_dir: str = "data"
    output_dir: str = "data/outputs"
    logs_dir: str = "logs"
    db_path: str = "data/app.db"
    log_level: str = "INFO"

    poll_interval_seconds: int = 15
    max_attempts: int = 3
    request_timeout_seconds: int = 300
    poll_timeout_seconds: int = 60
    download_timeout_seconds: int = 300

    max_download_size_mb: int = 2048
    max_reference_size_mb: int = 50

    pricing_stale_days: int = 14
    large_batch_warn_threshold: int = 50

    horde_anonymous: bool = True
    horde_steps: int = 20
    horde_max_dimension: int = 1024

    zip_include_metadata: bool = True

    # Secrets (read from .env / environment via pydantic-settings; never
    # stored in SQLite or returned to the frontend).
    agnes_api_key: str = ""
    ai_horde_api_key: str = ""

    # -- Derived paths -------------------------------------------------
    def data_dir_path(self) -> Path:
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    def output_dir_path(self) -> Path:
        p = Path(self.output_dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    def logs_dir_path(self) -> Path:
        p = Path(self.logs_dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    def db_path_resolved(self) -> Path:
        p = Path(self.db_path)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    def ensure_dirs(self) -> None:
        self.data_dir_path().mkdir(parents=True, exist_ok=True)
        self.output_dir_path().mkdir(parents=True, exist_ok=True)
        self.logs_dir_path().mkdir(parents=True, exist_ok=True)

    # -- Default rate limits (provider/model/type -> RPM) --------------
    DEFAULT_RATE_LIMITS: dict[str, int] = {
        "agnes_image:1k": 20,
        "agnes_image:2k": 10,
        "agnes_image:3k": 1,
        "agnes_image:4k": 1,
        "agnes_image:default": 20,
        "agnes_video:default": 1,
        "agnes_video:poll": 1,
        "ai_horde:submit": 10,
        "ai_horde:poll": 20,
        "ai_horde:default": 10,
    }

    def defaults_map(self) -> dict[str, Any]:
        return {
            "output_dir": self.output_dir,
            "poll_interval_seconds": self.poll_interval_seconds,
            "max_attempts": self.max_attempts,
            "request_timeout_seconds": self.request_timeout_seconds,
            "download_timeout_seconds": self.download_timeout_seconds,
            "max_download_size_mb": self.max_download_size_mb,
            "max_reference_size_mb": self.max_reference_size_mb,
            "pricing_stale_days": self.pricing_stale_days,
            "large_batch_warn_threshold": self.large_batch_warn_threshold,
            "free_only_mode": "true" if self.free_only_mode else "false",
            "horde_anonymous": "true" if self.horde_anonymous else "false",
            "horde_steps": str(self.horde_steps),
            "horde_max_dimension": str(self.horde_max_dimension),
            "zip_include_metadata": "true" if self.zip_include_metadata else "false",
            "log_level": self.log_level,
            "rate_limits": _dump_json(self.DEFAULT_RATE_LIMITS),
        }


def _dump_json(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


def get_agnes_api_key() -> str:
    """Return the Agnes API key from settings (.env file or environment)."""
    return (get_settings().agnes_api_key or os.environ.get("AGNES_API_KEY", "")).strip()


def get_horde_api_key() -> str:
    """Return the AI Horde API key from settings (.env file or environment).

    Falls back to the anonymous placeholder when nothing is configured.
    """
    key = (get_settings().ai_horde_api_key or os.environ.get("AI_HORDE_API_KEY", "")).strip()
    return key or "0000000000"
