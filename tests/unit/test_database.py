"""Unit tests for the SQLite database layer."""

from __future__ import annotations

import json

from app.database import Database, iso_to_epoch, utcnow
from app.security import CsrfProtector


def _job(i, provider="agnes_image", job_type="text_to_image"):
    return {
        "job_index": i,
        "type": job_type,
        "prompt": f"prompt {i}",
        "provider": provider,
        "requested_settings": {"size": "1K", "ratio": "1:1"},
    }


def _seed(db: Database) -> int:
    db.seed_provider_metadata(
        [{"provider": "agnes_image", "model": "m", "pricing_status": "free"}]
    )
    bid = db.create_batch("Test batch", "image", "ai_horde")
    db.create_jobs(bid, [_job(0), _job(1)])
    return bid


class TestSettings:
    def test_set_get_roundtrip(self, db):
        db.set_setting("k", "v")
        assert db.get_setting("k") == "v"
        db.set_setting("k", "v2")
        assert db.get_setting("k") == "v2"
        assert db.get_setting("missing") is None

    def test_all_settings(self, db):
        db.set_setting("a", "1")
        db.set_setting("b", "2")
        assert db.all_settings() == {"a": "1", "b": "2"}

    def test_csrf_persisted(self, db):
        c = CsrfProtector(db)
        c.check_env_setup()
        assert db.get_setting("csrf_secret") is not None


class TestProviderMetadata:
    def test_seed_and_read(self, db):
        db.seed_provider_metadata(
            [
                {"provider": "a", "model": "m1", "pricing_status": "free"},
                {"provider": "a", "model": "m2", "pricing_status": "paid",
                 "enabled": False},
            ]
        )
        rows = db.provider_metadata()
        assert len(rows) == 2
        by_model = {r["model"]: r for r in rows}
        assert by_model["m1"]["pricing_status"] == "free"
        assert by_model["m2"]["pricing_status"] == "paid"
        assert by_model["m2"]["enabled"] == 0

    def test_seed_updates_existing(self, db):
        db.seed_provider_metadata(
            [{"provider": "a", "model": "m", "pricing_status": "free"}]
        )
        db.seed_provider_metadata(
            [{"provider": "a", "model": "m", "pricing_status": "paid"}]
        )
        rows = db.provider_metadata()
        assert len(rows) == 1
        assert rows[0]["pricing_status"] == "paid"

    def test_touch_verified(self, db):
        db.seed_provider_metadata(
            [{"provider": "a", "model": "m", "pricing_status": "free"}]
        )
        db.touch_provider_verified("a", "m")
        rows = db.provider_metadata()
        assert rows[0]["last_verified"] is not None


class TestBatches:
    def test_create_and_get(self, db):
        bid = db.create_batch("b", "image", None)
        batch = db.get_batch(bid)
        assert batch["name"] == "b"
        assert batch["status"] == "running"
        assert batch["media_type"] == "image"

    def test_list_and_count(self, db):
        db.create_batch("b1", "image", None)
        db.create_batch("b2", "video", None)
        assert db.count_batches() == 2
        assert len(db.list_batches()) == 2

    def test_update_status(self, db):
        bid = db.create_batch("b", "image", None)
        db.update_batch_status(bid, "paused")
        assert db.get_batch(bid)["status"] == "paused"

    def test_counts(self, db):
        bid = _seed(db)
        done, failed = db.batch_counts(bid)
        assert (done, failed) == (0, 0)

    def test_acknowledged(self, db):
        bid = db.create_batch("b", "image", None, pricing_acknowledged=True)
        assert db.get_batch(bid)["pricing_acknowledged"] == 1
        db.set_batch_acknowledged(bid)
        assert db.get_batch(bid)["pricing_acknowledged"] == 1


class TestJobs:
    def test_create_and_count(self, db):
        bid = _seed(db)
        assert db.count_jobs(bid) == 2
        assert db.count_jobs_by_status(bid, "pending") == 2

    def test_job_counts_all(self, db):
        _seed(db)
        assert db.job_counts_all() == {"pending": 2}

    def test_list_jobs_filtered(self, db):
        bid = _seed(db)
        jobs = db.list_jobs(bid, status="pending")
        assert len(jobs) == 2
        assert db.list_jobs(bid, status="completed") == []

    def test_claim_job_atomic(self, db):
        _seed(db)
        job = db.pick_next_pending_job(utcnow())
        assert job is not None
        assert db.claim_job(job["id"]) is True
        assert db.claim_job(job["id"]) is False  # already claimed
        assert db.get_job(job["id"])["status"] == "processing"
        assert db.get_job(job["id"])["attempts"] == 1

    def test_pick_next_pending_job_respects_retry_at(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.schedule_retry(job["id"], "2999-01-01T00:00:00", "rate_limit", "x")
        next_job = db.pick_next_pending_job(utcnow())
        assert next_job is not None
        assert next_job["id"] != job["id"]

    def test_fail_and_retry_failed(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.fail_job(job["id"], "auth", "bad key")
        row = db.get_job(job["id"])
        assert row["status"] == "failed"
        assert row["error_type"] == "auth"
        n = db.retry_failed_jobs(bid)
        assert n == 1
        assert db.get_job(job["id"])["status"] == "pending"

    def test_finalize_job(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.finalize_job(job["id"], "out/0001.png", {"size": "1K"}, "https://x", "m")
        row = db.get_job(job["id"])
        assert row["status"] == "completed"
        assert row["local_output_path"] == "out/0001.png"
        assert json.loads(row["actual_settings"]) == {"size": "1K"}
        done, _ = db.batch_counts(bid)
        assert done == 1

    def test_cancel_job(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.cancel_job(job["id"])
        assert db.get_job(job["id"])["status"] == "cancelled"

    def test_set_submitted_remote(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.set_job_submitted_remote(job["id"], "task-1", "video-1",
                                    {"w": 1}, "m")
        row = db.get_job(job["id"])
        assert row["status"] == "queued_remote"
        assert row["remote_task_id"] == "task-1"
        assert row["remote_video_id"] == "video-1"

    def test_polling_transitions(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.set_job_polling(job["id"], progress=50)
        assert db.get_job(job["id"])["status"] == "polling"
        db.update_polling_actual(job["id"], "queued_remote", {"p": 1})
        assert db.get_job(job["id"])["status"] == "queued_remote"
        assert json.loads(db.get_job(job["id"])["actual_settings"]) == {"p": 1}

    def test_reset_job_to_pending(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.claim_job(job["id"])
        db.reset_job_to_pending(job["id"])
        assert db.get_job(job["id"])["status"] == "pending"

    def test_reset_job_for_retry(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.set_job_submitted_remote(job["id"], "t", None, {}, "m")
        db.reset_job_for_retry(job["id"])
        row = db.get_job(job["id"])
        assert row["status"] == "pending"
        assert row["remote_task_id"] is None
        assert row["attempts"] == 0

    def test_switch_fallback(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.switch_fallback(job["id"], "ai_horde", None)
        row = db.get_job(job["id"])
        assert row["provider"] == "ai_horde"
        assert row["status"] == "pending"
        assert json.loads(row["requested_settings"])["_fallback_used"] is True

    def test_mark_batch_cancelled_jobs(self, db):
        bid = _seed(db)
        rows = db.mark_batch_cancelled_jobs(bid)
        assert len(rows) == 2
        assert all(r["status"] in ("pending",) for r in rows)
        assert db.count_jobs_by_status(bid, "cancelled") == 2

    def test_pick_remote_jobs(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.set_job_submitted_remote(job["id"], "t", None, {}, "m")
        remote = db.pick_remote_jobs()
        assert len(remote) == 1
        assert remote[0]["id"] == job["id"]


class TestEvents:
    def test_log_and_query(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.log_event(job["id"], "agnes_image", "submit", {"size": "1K"})
        db.log_event(None, "app", "startup")
        events = db.events_for_job(job["id"])
        assert len(events) == 1
        assert events[0]["event_type"] == "submit"
        assert json.loads(events[0]["metadata"]) == {"size": "1K"}
        recent = db.recent_events()
        assert len(recent) == 2


class TestReconcile:
    def test_stale_processing_reconciled(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.claim_job(job["id"])
        n = db.reconcile_stale_processing()
        assert n == 1
        assert db.get_job(job["id"])["status"] == "pending"

    def test_stuck_polling_to_queued(self, db):
        bid = _seed(db)
        job = db.list_jobs(bid)[0]
        db.set_job_polling(job["id"])
        n = db.reset_stuck_polling_to_queued()
        assert n == 1
        assert db.get_job(job["id"])["status"] == "queued_remote"

    def test_find_part_files(self, db, tmp_path):
        (tmp_path / "x.part").write_text("data")
        (tmp_path / "y.png").write_text("data")
        parts = db.find_part_files(tmp_path)
        assert [p.name for p in parts] == ["x.part"]


class TestDuplicate:
    def test_duplicate_batch(self, db):
        bid = _seed(db)
        new_id = db.duplicate_batch(bid)
        assert new_id != bid
        assert db.count_jobs(new_id) == 2
        assert db.get_batch(new_id)["name"].endswith("(copy)")

    def test_duplicate_missing(self, db):
        import pytest

        with pytest.raises(ValueError):
            db.duplicate_batch(999)


class TestTimeHelpers:
    def test_utcnow_iso(self):
        assert utcnow().endswith("Z") or "+" in utcnow()

    def test_iso_to_epoch(self):
        assert iso_to_epoch("2024-01-01T00:00:00+00:00") is not None
        assert iso_to_epoch("garbage") is None
        assert iso_to_epoch(None) is None
