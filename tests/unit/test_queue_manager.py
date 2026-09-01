"""Unit tests for the queue manager (retries, fallback, output handling)."""

from __future__ import annotations

import asyncio

import httpx
from app.providers.base import ProviderError, SubmitResult
from app.queue_manager import QueueManager, _iso_add_seconds
from app.runtime import RuntimeConfig


def _fake_runtime(db, settings):
    rt = RuntimeConfig(db, settings)
    rt.load()
    return rt


def _seed_free(db):
    db.seed_provider_metadata(
        [
            {"provider": "agnes_image", "model": "m", "pricing_status": "free"},
            {"provider": "ai_horde", "model": "m", "pricing_status": "free"},
        ]
    )


def _job_dict(i, provider="agnes_image", **overrides):
    job = {
        "job_index": i,
        "type": "text_to_image",
        "prompt": f"prompt {i}",
        "provider": provider,
        "fallback_provider": None,
        "max_attempts": 3,
        "requested_settings": {"size": "1K", "ratio": "1:1"},
    }
    job.update(overrides)
    return job


class FakeProvider:
    name = "fake"
    display_name = "Fake"
    media_types = ("image",)
    supports_async = False

    def __init__(self, db, submit_result=None, submit_error=None,
                 poll_result=None, output_mime=None):
        self.db = db
        self.submit_result = submit_result
        self.submit_error = submit_error
        self.poll_result = poll_result
        self.submits = 0
        self._output_mime = output_mime or {"image/png"}
        self.cancels = 0

    async def healthcheck(self):  # pragma: no cover
        from app.providers.base import HealthStatus

        return HealthStatus(state="reachable", reachable=True)

    async def validate_job(self, job):
        return None

    async def submit(self, job):
        self.submits += 1
        if self.submit_error:
            raise self.submit_error
        return self.submit_result or SubmitResult(status="processing", b64_output=None)

    async def poll(self, job):
        if isinstance(self.poll_result, Exception):
            raise self.poll_result
        return self.poll_result

    async def cancel(self, job):
        self.cancels += 1

    def output_mime(self):
        return self._output_mime

    def expected_extension(self, job, actual):
        return "png"


class TestQueueManager:
    def _qm(self, db, runtime, providers):
        return QueueManager(db, runtime, None, providers, httpx.AsyncClient())

    # -- startup reconcile -----------------------------------------------
    def test_reconcile_on_startup(self, db, settings, tmp_path):
        settings.output_dir_path().mkdir(parents=True, exist_ok=True)
        (settings.output_dir_path() / "x.part").write_text("junk")
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        db.claim_job(db.list_jobs(bid)[0]["id"])
        qm = self._qm(db, _fake_runtime(db, settings), {})
        result = qm.reconcile_on_startup()
        assert result["processing"] == 1
        assert result["parts"] == 1

    # -- process job: happy path (sync output) ---------------------------
    async def test_process_job_sync_completes(self, db, settings, tmp_path):
        settings.output_dir_path().mkdir(parents=True, exist_ok=True)
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]

        out = settings.output_dir_path() / str(bid)
        out.mkdir(parents=True, exist_ok=True)
        (out / "0001_prompt_0.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

        provider = FakeProvider(
            db,
            submit_result=SubmitResult(
                status="processing",
                b64_output=__import__("base64").b64encode(
                    b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
                ).decode(),
                actual_settings={"size": "1K"},
            ),
        )
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        db.claim_job(job["id"])
        await qm._process_job(job["id"])
        row = db.get_job(job["id"])
        assert row["status"] == "completed"
        assert row["local_output_path"] is not None
        assert db.batch_counts(bid) == (1, 0)

    # -- process job: unknown provider -----------------------------------
    async def test_process_job_unknown_provider(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0, provider="ghost")])
        job = db.list_jobs(bid)[0]
        qm = self._qm(db, _fake_runtime(db, settings), {})
        db.claim_job(job["id"])
        await qm._process_job(job["id"])
        assert db.get_job(job["id"])["status"] == "failed"

    # -- process job: paused batch resets to pending ---------------------
    async def test_process_job_batch_paused(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        db.update_batch_status(bid, "paused")
        qm = self._qm(db, _fake_runtime(db, settings),
                      {"agnes_image": FakeProvider(db)})
        db.claim_job(job["id"])
        await qm._process_job(job["id"])
        assert db.get_job(job["id"])["status"] == "pending"

    # -- process job: non-retryable submit error -> fail -----------------
    async def test_process_job_auth_error_fails(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        provider = FakeProvider(db, submit_error=ProviderError("auth", "bad key"))
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        db.claim_job(job["id"])
        await qm._process_job(job["id"])
        assert db.get_job(job["id"])["status"] == "failed"
        assert db.get_job(job["id"])["error_type"] == "auth"

    # -- process job: retryable error schedules retry --------------------
    async def test_process_job_rate_limit_retries(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0, max_attempts=3)])
        job = db.list_jobs(bid)[0]
        provider = FakeProvider(db, submit_error=ProviderError("rate_limit", "429"))
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        db.claim_job(job["id"])
        await qm._process_job(job["id"])
        row = db.get_job(job["id"])
        assert row["status"] == "pending"
        assert row["retry_at"] is not None
        assert row["error_type"] == "rate_limit"

    # -- process job: retries exhausted -> fallback ----------------------
    async def test_process_job_fallback_after_exhaustion(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0, max_attempts=1, fallback_provider="ai_horde")])
        job = db.list_jobs(bid)[0]
        agnes = FakeProvider(db, submit_error=ProviderError("server", "5xx"))
        horde = FakeProvider(db)
        qm = self._qm(db, _fake_runtime(db, settings),
                      {"agnes_image": agnes, "ai_horde": horde})
        db.claim_job(job["id"])
        await qm._process_job(job["id"])
        row = db.get_job(job["id"])
        assert row["provider"] == "ai_horde"
        assert row["status"] == "pending"  # switched for retry

    # -- process job: no fallback after exhaustion -> fail ---------------
    async def test_process_job_fails_after_exhaustion(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0, max_attempts=1)])
        job = db.list_jobs(bid)[0]
        provider = FakeProvider(db, submit_error=ProviderError("server", "5xx"))
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        db.claim_job(job["id"])
        await qm._process_job(job["id"])
        row = db.get_job(job["id"])
        assert row["status"] == "failed"
        assert row["error_type"] == "server"

    # -- process job: queued_remote --------------------------------------
    async def test_process_job_queued_remote(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        provider = FakeProvider(
            db,
            submit_result=SubmitResult(
                status="queued_remote", remote_task_id="t-1",
                actual_settings={"m": "x"},
            ),
        )
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        db.claim_job(job["id"])
        await qm._process_job(job["id"])
        row = db.get_job(job["id"])
        assert row["status"] == "queued_remote"
        assert row["remote_task_id"] == "t-1"

    # -- process job: pricing guard blocks -------------------------------
    async def test_process_job_pricing_guard_blocks(self, db, settings):
        # provider not seeded as free -> guard blocks
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        qm = self._qm(db, _fake_runtime(db, settings),
                      {"agnes_image": FakeProvider(db)})
        db.claim_job(job["id"])
        await qm._process_job(job["id"])
        row = db.get_job(job["id"])
        assert row["status"] == "failed"
        assert row["error_type"] in ("unknown_pricing", "not_free")

    # -- poll job ----------------------------------------------------------
    async def test_poll_completed_downloads(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        db.set_job_submitted_remote(job["id"], "t-1", None, {}, "m")

        out = settings.output_dir_path() / str(bid)
        out.mkdir(parents=True, exist_ok=True)
        (out / "0001_prompt_0.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

        from app.providers.base import PollResult

        provider = FakeProvider(
            db,
            poll_result=PollResult(
                status="completed",
                b64_output=__import__("base64").b64encode(
                    b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
                ).decode(),
                actual_settings={"size": "1K"},
            ),
        )
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        await qm._poll_job(db.get_job(job["id"]))
        assert db.get_job(job["id"])["status"] == "completed"

    async def test_poll_queued_keeps_queued(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        db.set_job_submitted_remote(job["id"], "t-1", None, {}, "m")

        from app.providers.base import PollResult

        provider = FakeProvider(db, poll_result=PollResult(status="queued", progress=10))
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        await qm._poll_job(db.get_job(job["id"]))
        assert db.get_job(job["id"])["status"] == "queued_remote"

    async def test_poll_transient_error_repolls(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        db.set_job_submitted_remote(job["id"], "t-1", None, {}, "m")
        provider = FakeProvider(db, poll_result=ProviderError("network", "boom"))
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        await qm._poll_job(db.get_job(job["id"]))
        assert db.get_job(job["id"])["status"] == "queued_remote"

    async def test_poll_failed_marks_failed(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        db.set_job_submitted_remote(job["id"], "t-1", None, {}, "m")

        from app.providers.base import PollResult

        provider = FakeProvider(db, poll_result=PollResult(status="failed", error="no"))
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        await qm._poll_job(db.get_job(job["id"]))
        assert db.get_job(job["id"])["status"] == "failed"

    # -- batch controls ---------------------------------------------------
    def test_batch_controls(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        qm = self._qm(db, _fake_runtime(db, settings), {})
        qm.pause_batch(bid)
        assert db.get_batch(bid)["status"] == "paused"
        qm.resume_batch(bid)
        assert db.get_batch(bid)["status"] == "running"

    def test_cancel_batch(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        provider = FakeProvider(db)
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        qm.cancel_batch(bid)
        assert db.get_batch(bid)["status"] == "cancelled"
        assert db.count_jobs_by_status(bid, "cancelled") == 1

    async def test_cancel_batch_calls_provider_cancel(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        db.set_job_submitted_remote(job["id"], "t-1", None, {}, "m")
        provider = FakeProvider(db)
        qm = self._qm(db, _fake_runtime(db, settings), {"agnes_image": provider})
        qm.cancel_batch(bid)
        await asyncio.sleep(0)
        assert provider.cancels == 1

    def test_retry_failed_batch(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        db.fail_job(job["id"], "auth", "x")
        qm = self._qm(db, _fake_runtime(db, settings), {})
        n = qm.retry_failed_batch(bid)
        assert n == 1
        assert db.get_job(job["id"])["status"] == "pending"

    def test_retry_job(self, db, settings):
        _seed_free(db)
        bid = db.create_batch("b", "image", None)
        db.create_jobs(bid, [_job_dict(0)])
        job = db.list_jobs(bid)[0]
        db.fail_job(job["id"], "auth", "x")
        qm = self._qm(db, _fake_runtime(db, settings), {})
        qm.retry_job(job["id"])
        assert db.get_job(job["id"])["status"] == "pending"

    async def test_start_stop(self, db, settings):
        _seed_free(db)
        qm = self._qm(db, _fake_runtime(db, settings), {})
        qm.start()
        await qm.stop()

    async def test_iso_add_seconds(self):
        assert _iso_add_seconds(60).endswith("+00:00") or "T" in _iso_add_seconds(60)
