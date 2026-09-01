"""Unit tests for RuntimeConfig."""

from __future__ import annotations

import json

from app.runtime import RuntimeConfig


class TestRuntimeConfig:
    def test_load_seeds_defaults(self, runtime):
        assert runtime.get("free_only_mode") == "true"
        assert int(runtime.get_int("poll_interval_seconds", 0)) >= 1
        assert runtime.get_bool("free_only_mode", False) is True

    def test_stored_overrides_default(self, db, settings):
        rt = RuntimeConfig(db, settings)
        rt.load()
        rt.set_many({"poll_interval_seconds": "7"})
        rt2 = RuntimeConfig(db, settings)
        rt2.load()
        assert rt2.get("poll_interval_seconds") == "7"

    def test_set_many_serializes_complex(self, db, settings):
        rt = RuntimeConfig(db, settings)
        rt.load()
        rt.set_many({"rate_limits": {"a": 1.5}, "free_only_mode": False})
        assert rt.get_bool("free_only_mode", True) is False
        parsed = json.loads(rt.get("rate_limits"))
        assert parsed == {"a": 1.5}

    def test_get_int_fallback(self, runtime):
        assert runtime.get_int("not-an-int", 42) == 42

    def test_get_bool_variants(self, db, settings):
        rt = RuntimeConfig(db, settings)
        rt.load()
        rt.set_many({"a": "yes", "b": "off", "c": "0", "d": "1", "e": "weird"})
        assert rt.get_bool("a", False) is True
        assert rt.get_bool("b", True) is False
        assert rt.get_bool("c", True) is False
        assert rt.get_bool("d", False) is True
        assert rt.get_bool("e", True) is True  # invalid -> default

    def test_rate_limits(self, runtime):
        limits = runtime.rate_limits()
        assert isinstance(limits, dict)
        assert "agnes_image:1k" in limits

    def test_as_dict_and_public(self, runtime):
        asd = runtime.as_dict()
        assert isinstance(asd, dict)
        pub = runtime.public_dict()
        assert isinstance(pub["rate_limits"], dict)

    def test_get_missing_default(self, runtime):
        assert runtime.get("nope") is None
        assert runtime.get("nope", "x") == "x"
