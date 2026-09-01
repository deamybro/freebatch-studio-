"""Unit tests for the FREE-ONLY pricing guard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.pricing import (
    PricingError,
    check_free_only,
    get_provider_meta,
    is_free,
    large_batch_requires_ack,
    pricing_stale,
    stale_warning_message,
)


def _meta(pricing_status="free", enabled=True, last_verified=None):
    m = {
        "provider": "agnes_image",
        "model": "agnes-image-2.1-flash",
        "pricing_status": pricing_status,
        "enabled": enabled,
    }
    if last_verified is not None:
        m["last_verified"] = last_verified
    return m


class TestIsFree:
    def test_free_enabled(self):
        assert is_free(_meta("free", True)) is True

    def test_not_free(self):
        assert is_free(_meta("paid", True)) is False

    def test_disabled(self):
        assert is_free(_meta("free", False)) is False

    def test_none(self):
        assert is_free(None) is False


class TestPricingStale:
    def test_missing_meta_stale(self):
        assert pricing_stale(None, 14) is True

    def test_no_verified_date_stale(self):
        assert pricing_stale(_meta(), 14) is True

    def test_fresh_not_stale(self):
        now = datetime.now(UTC)
        m = _meta(last_verified=now.isoformat())
        assert pricing_stale(m, 14) is False

    def test_old_stale(self):
        old = datetime.now(UTC) - timedelta(days=30)
        m = _meta(last_verified=old.isoformat())
        assert pricing_stale(m, 14) is True

    def test_invalid_date_stale(self):
        m = _meta(last_verified="not-a-date")
        assert pricing_stale(m, 14) is True


class TestCheckFreeOnly:
    def test_free_provider_passes(self, db):
        db.seed_provider_metadata(
            [{"provider": "agnes_image", "model": "m", "pricing_status": "free"}]
        )
        check_free_only(db, "agnes_image", "m", free_only_mode=True)

    def test_unknown_provider_raises(self, db):
        with pytest.raises(PricingError) as ei:
            check_free_only(db, "nope", "m", free_only_mode=True)
        assert ei.value.code == "unknown_pricing"

    def test_not_free_raises(self, db):
        db.seed_provider_metadata(
            [{"provider": "agnes_image", "model": "m", "pricing_status": "paid"}]
        )
        with pytest.raises(PricingError) as ei:
            check_free_only(db, "agnes_image", "m", free_only_mode=True)
        assert ei.value.code == "not_free"

    def test_free_only_off_still_requires_known(self, db):
        with pytest.raises(PricingError) as ei:
            check_free_only(db, "nope", "m", free_only_mode=False)
        assert ei.value.code == "unknown_pricing"


def _make_stale(db):
    """Seed a free provider whose pricing verification is old."""
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    db.seed_provider_metadata(
        [{"provider": "agnes_image", "model": "m", "pricing_status": "free"}]
    )
    db._run(
        "UPDATE provider_metadata SET last_verified=? WHERE provider=? AND model=?",
        (old, "agnes_image", "m"),
    )


class TestLargeBatchAck:
    def test_small_batch_no_ack(self, db):
        db.seed_provider_metadata(
            [{"provider": "agnes_image", "model": "m", "pricing_status": "free"}]
        )
        assert large_batch_requires_ack(db, jobs_count=5, large_threshold=50,
                                        stale_days=14) is False

    def test_large_batch_with_fresh_pricing_no_ack(self, db):
        db.seed_provider_metadata(
            [{"provider": "agnes_image", "model": "m", "pricing_status": "free",
              "last_verified": datetime.now(UTC).isoformat()}]
        )
        assert large_batch_requires_ack(db, jobs_count=100, large_threshold=50,
                                        stale_days=14) is False

    def test_large_batch_with_stale_pricing_requires_ack(self, db):
        _make_stale(db)
        assert large_batch_requires_ack(db, jobs_count=100, large_threshold=50,
                                        stale_days=14) is True


class TestStaleWarningMessage:
    def test_none_when_fresh(self, db):
        db.seed_provider_metadata(
            [{"provider": "agnes_image", "model": "m", "pricing_status": "free",
              "last_verified": datetime.now(UTC).isoformat()}]
        )
        assert stale_warning_message(db, 14) is None

    def test_message_when_stale(self, db):
        _make_stale(db)
        msg = stale_warning_message(db, 14)
        assert msg is not None
        assert "agnes_image" in msg


class TestGetProviderMeta:
    def test_match_by_provider(self, db):
        db.seed_provider_metadata(
            [{"provider": "ai_horde", "model": "x", "pricing_status": "free"}]
        )
        meta = get_provider_meta(db, "ai_horde")
        assert meta is not None
        assert meta["provider"] == "ai_horde"

    def test_no_match(self, db):
        assert get_provider_meta(db, "missing") is None
