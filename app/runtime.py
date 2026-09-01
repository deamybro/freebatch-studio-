"""Runtime configuration backed by the settings table.

Non-secret settings are stored in SQLite and exposed through the settings API.
The queue manager and providers read the live values through this class.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from app.config import BASE_DIR, Settings
from app.database import Database


class RuntimeConfig:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._cache: dict[str, str] = {}

    def load(self) -> None:
        """Seed defaults then overlay stored values."""
        defaults = self.settings.defaults_map()
        stored = self.db.all_settings()
        self._cache = {**defaults, **stored}
        # Re-apply the dynamic rate-limit defaults if nothing stored yet.
        if "rate_limits" not in stored:
            self._cache["rate_limits"] = json.dumps(
                self.settings.DEFAULT_RATE_LIMITS
            )

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self._cache:
            return default
        return self._cache[key]

    def get_int(self, key: str, default: int) -> int:
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool) -> bool:
        val = str(self.get(key, default)).strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
        if val in ("0", "false", "no", "off"):
            return False
        return bool(default)

    def set_many(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, bool):
                value = "true" if value else "false"
            self.db.set_setting(key, str(value))
            self._cache[key] = str(value)

    def set(self, key: str, value: Any) -> None:
        self.set_many({key: value})

    def rate_limits(self) -> dict[str, float]:
        raw = self.get("rate_limits", "{}")
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): float(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            pass
        return dict(self.settings.DEFAULT_RATE_LIMITS)

    def output_dir_path(self) -> Path:
        """Resolve the live output directory (env default, DB overrides it)."""
        raw = str(self.get("output_dir") or self.settings.output_dir)
        p = Path(raw)
        if not p.is_absolute():
            p = BASE_DIR / p
        return p

    def as_dict(self) -> dict[str, str]:
        return dict(self._cache)

    def public_dict(self) -> dict[str, Any]:
        """Settings view safe to expose (no secrets live here anyway)."""
        out = dict(self._cache)
        if "rate_limits" in out:
            with contextlib.suppress(ValueError, TypeError):
                out["rate_limits"] = json.loads(out["rate_limits"])

        for key, value in list(out.items()):
            with contextlib.suppress(ValueError, TypeError):
                out[key] = json.loads(value)
        return out
