"""Queue manager: single worker + remote-job poller with restart recovery.

Design notes:
  * Idempotent by construction - jobs are claimed atomically
    (``UPDATE ... WHERE status='pending'``) so a restart can never process the
    same job twice.
  * On startup, stale local ``processing`` states are reconciled back to
    ``pending``; remote jobs keep their remote ids and resume polling.
  * Retries use exponential backoff + jitter; retryable technical failures
    (429/timeout/network/5xx) are retried and may trigger a configured
    fallback provider. Content-policy/invalid/auth errors never retry or
    fall back.
  * FREE-ONLY mode is enforced before every submission.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC
from typing import Any

from app.database import Database, utcnow
from app.downloader import (
    DownloadError,
    cleanup_part_files,
    stream_download,
    write_base64_image,
)
from app.manifest import batch_output_dir, write_manifest
from app.pricing import PricingError, check_free_only
from app.providers.base import (
    NON_RETRYABLE_CATEGORIES,
    ProviderError,
    backoff_seconds,
)
from app.rate_limiter import RateLimiter
from app.runtime import RuntimeConfig
from app.security import safe_basename_for_job

logger = logging.getLogger("freebatch")


def _parse_job_settings(job: dict) -> dict:
    """Parse the JSON-string settings columns the DB stores into dicts.

    Providers expect ``requested_settings`` / ``actual_settings`` to be dicts;
    the database persists them as JSON text, so they must be decoded before a
    job is handed to a provider.
    """
    for key in ("requested_settings", "actual_settings"):
        val = job.get(key)
        if isinstance(val, str):
            try:
                job[key] = json.loads(val or "{}")
            except (ValueError, TypeError):
                job[key] = {}
        elif val is None:
            job[key] = {}
    return job


class QueueManager:
    def __init__(
        self,
        db: Database,
        runtime: RuntimeConfig,
        rate_limiter: RateLimiter,
        providers: dict[str, Any],
        client: Any,
    ) -> None:
        self.db = db
        self.runtime = runtime
        self.rate_limiter = rate_limiter
        self.providers = providers
        self.client = client
        self._stop = False
        self._tasks: list[asyncio.Task] = []
        self._cancel_tasks: list[asyncio.Task] = []

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._stop = False
        self._tasks = [
            loop.create_task(self._worker_loop(), name="freebatch-worker"),
            loop.create_task(self._poller_loop(), name="freebatch-poller"),
        ]

    async def stop(self) -> None:
        self._stop = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def reconcile_on_startup(self) -> dict[str, int]:
        """Restart recovery: reconcile stale states and clean .part files."""
        processing = self.db.reconcile_stale_processing()
        polling = self.db.reset_stuck_polling_to_queued()
        parts = cleanup_part_files(self.runtime.output_dir_path())
        logger.info(
            "startup reconcile: %d stale processing -> pending, %d polling -> "
            "queued_remote, %d .part files removed",
            processing, polling, parts,
        )
        return {"processing": processing, "polling": polling, "parts": parts}

    # -- worker ----------------------------------------------------------
    async def _worker_loop(self) -> None:
        logger.info("queue worker started")
        while not self._stop:
            try:
                job = self.db.pick_next_pending_job(utcnow())
                if job is None:
                    await asyncio.sleep(1.0)
                    continue
                claimed = self.db.claim_job(job["id"])
                if not claimed:
                    continue
                await self._process_job(job["id"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker loop error")
                await asyncio.sleep(2.0)

    async def _process_job(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if not job or job["status"] != "processing":
            return
        job = _parse_job_settings(dict(job))
        batch = self.db.get_batch(job["batch_id"])
        if not batch:
            self._fail_job(job_id, "internal", "batch missing")
            return
        if batch["status"] != "running":
            # Batch paused/cancelled between claim and process.
            self.db.reset_job_to_pending(job_id)
            return

        provider = self.providers.get(job["provider"])
        if provider is None:
            self._fail_job(job_id, "provider", f"unknown provider {job['provider']}")
            return

        # FREE-ONLY guard (always runs before any submission).
        try:
            check_free_only(
                self.db, job["provider"], job["model"],
                self.runtime.get_bool("free_only_mode", True),
            )
        except PricingError as exc:
            self._fail_job(job_id, exc.code, exc.message)
            logger.warning("job %s blocked by pricing guard: %s", job_id, exc)
            return

        try:
            await provider.validate_job(job)
        except ProviderError as exc:
            self._fail_job(job_id, exc.category, exc.message)
            return

        attempts = int(job["attempts"])
        max_attempts = int(job["max_attempts"] or self.runtime.get_int("max_attempts", 3))

        try:
            result = await provider.submit(job)
        except ProviderError as exc:
            await self._handle_submit_error(job_id, job, exc, attempts, max_attempts)
            return
        except Exception as exc:
            logger.exception("submit raised unexpected error for job %s", job_id)
            self._fail_job(job_id, "internal", f"unexpected error: {exc}")
            return

        if result.status == "queued_remote":
            self.db.set_job_submitted_remote(
                job_id, result.remote_task_id, result.remote_video_id,
                result.actual_settings, result.model,
            )
            logger.info(
                "job %s submitted to %s (task=%s video=%s)",
                job_id, provider.name, result.remote_task_id, result.remote_video_id,
            )
            return

        # Synchronous result (Agnes Image): download / decode now.
        try:
            await self._handle_output(
                job_id, remote_url=result.remote_output_url,
                b64=result.b64_output, actual=result.actual_settings,
                model=result.model,
            )
        except DownloadError as exc:
            await self._handle_download_failure(job_id, job, exc, attempts, max_attempts)
        except Exception as exc:
            logger.exception("output handling failed for job %s", job_id)
            self._fail_job(job_id, "internal", f"unexpected error: {exc}")

    async def _handle_submit_error(
        self, job_id: int, job: Any, exc: ProviderError,
        attempts: int, max_attempts: int,
    ) -> None:
        logger.warning(
            "job %s submit error: %s (attempts %d/%d)",
            job_id, exc, attempts, max_attempts,
        )
        self.db.log_event(job_id, job["provider"], "submit_error",
                          {"category": exc.category, "detail": exc.message})
        if exc.category in NON_RETRYABLE_CATEGORIES:
            self._fail_job(job_id, exc.category, exc.message)
            return
        if attempts < max_attempts:
            delay = backoff_seconds(attempts)
            if exc.retry_after:
                delay = max(delay, float(exc.retry_after))
            self.db.schedule_retry(
                job_id, _iso_add_seconds(delay), exc.category, exc.message,
            )
            logger.info("job %s retrying in %ss (%s)", job_id, delay, exc.category)
            return
        # Attempts exhausted on a technical failure -> fallback.
        if await self._try_fallback(job_id, job, exc):
            return
        self._fail_job(job_id, exc.category, exc.message)

    async def _try_fallback(self, job_id: int, job: Any, exc: ProviderError) -> bool:
        fallback = job["fallback_provider"]
        req = job["requested_settings"]
        if isinstance(req, str):
            req = json.loads(req or "{}")
        if not fallback or fallback == job["provider"] or req.get("_fallback_used"):
            return False
        provider = self.providers.get(fallback)
        if provider is None:
            return False
        # Fallback is for technical availability only - never content policy.
        if exc.category in NON_RETRYABLE_CATEGORIES:
            return False
        try:
            check_free_only(
                self.db, fallback, None,
                self.runtime.get_bool("free_only_mode", True),
            )
        except PricingError:
            logger.warning("fallback %s blocked by pricing guard", fallback)
            return False
        self.db.log_event(job_id, job["provider"], "fallback_switch",
                          {"to": fallback, "reason": exc.category})
        self.db.switch_fallback(job_id, fallback, None)
        logger.info("job %s switched to fallback provider %s", job_id, fallback)
        return True

    async def _handle_download_failure(
        self, job_id: int, job: Any, exc: Exception,
        attempts: int, max_attempts: int,
    ) -> None:
        err = str(exc)
        if attempts < max_attempts:
            delay = backoff_seconds(attempts)
            self.db.schedule_retry(job_id, _iso_add_seconds(delay), "download", err)
            return
        await self._try_fallback(job_id, job, ProviderError("network", err))
        if self.db.get_job(job_id)["status"] == "pending":
            return
        self._fail_job(job_id, "download", err)

    # -- output handling -------------------------------------------------
    async def _handle_output(
        self, job_id: int, *, remote_url: str | None, b64: str | None,
        actual: dict, model: str | None,
    ) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return
        job = _parse_job_settings(dict(job))
        out_root = self.runtime.output_dir_path()
        out_dir = batch_output_dir(job["batch_id"], out_root)
        out_dir.mkdir(parents=True, exist_ok=True)
        extension = self.providers[job["provider"]].expected_extension(job, actual)
        filename = safe_basename_for_job(int(job["job_index"]), job["prompt"], extension)

        max_download_mb = self.runtime.get_int("max_download_size_mb", 2048)
        timeout = self.runtime.get_int("download_timeout_seconds", 300)
        allowed = self.providers[job["provider"]].output_mime()

        if b64:
            path = write_base64_image(
                b64, out_dir, filename,
                max_size_mb=max_download_mb, allowed_mimes=allowed,
            )
        elif remote_url:
            path = await stream_download(
                self.client, remote_url, out_dir, filename,
                max_size_mb=max_download_mb, timeout_seconds=timeout,
                allowed_mimes=allowed,
            )
        else:
            raise DownloadError("no output url or base64 payload from provider")

        rel = path.relative_to(out_root) if path.is_relative_to(out_root) else path
        self._finalize_job(
            job_id, str(rel), actual, remote_url, model,
        )
        logger.info("job %s completed -> %s", job_id, path)
        self.db.refresh_batch_counts(job["batch_id"])

    # -- manifest helpers -------------------------------------------------
    def _sync_manifest(self, batch_id: int) -> None:
        try:
            write_manifest(self.db, batch_id, out_root=self.runtime.output_dir_path())
        except Exception:
            logger.exception("manifest write failed for batch %s", batch_id)

    def _fail_job(self, job_id: int, error_type: str, error_message: str) -> None:
        self.db.fail_job(job_id, error_type, error_message)
        job = self.db.get_job(job_id)
        if job:
            self._sync_manifest(job["batch_id"])

    def _finalize_job(
        self, job_id: int, local_path: str, actual_settings: dict,
        remote_output_url: str | None, model: str | None,
    ) -> None:
        self.db.finalize_job(
            job_id, local_path, actual_settings, remote_output_url, model,
        )
        job = self.db.get_job(job_id)
        if job:
            self._sync_manifest(job["batch_id"])

    # -- poller ----------------------------------------------------------
    async def _poller_loop(self) -> None:
        logger.info("queue poller started")
        while not self._stop:
            try:
                interval = self.runtime.get_int("poll_interval_seconds", 15)
                jobs = self.db.pick_remote_jobs()
                for job in jobs:
                    try:
                        await self._poll_job(job)
                    except Exception:
                        logger.exception("poll error for job %s", job["id"])
                await asyncio.sleep(max(2, interval))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("poller loop error")
                await asyncio.sleep(5.0)

    async def _poll_job(self, job: Any) -> None:
        job = _parse_job_settings(dict(job))
        provider = self.providers.get(job["provider"])
        if provider is None:
            self._fail_job(job["id"], "provider", f"unknown provider {job['provider']}")
            return
        try:
            result = await provider.poll(job)
        except ProviderError as exc:
            if exc.category in NON_RETRYABLE_CATEGORIES:
                self._fail_job(job["id"], exc.category, exc.message)
            else:
                # Transient - keep status queued_remote and re-poll next cycle.
                self.db.update_polling_actual(job["id"], "queued_remote", None)
                logger.info(
                    "job %s poll transient error (%s); will re-poll", job["id"], exc,
                )
            return

        if result.status == "queued":
            self.db.update_polling_actual(job["id"], "queued_remote", result.actual_settings)
            return
        if result.status == "processing":
            self.db.update_polling_actual(job["id"], "polling", result.actual_settings)
            return
        if result.status == "failed":
            self._fail_job(job["id"], "provider", result.error or "provider failed")
            self.db.refresh_batch_counts(job["batch_id"])
            return
        if result.status == "cancelled":
            self.db.cancel_job(job["id"])
            self._sync_manifest(job["batch_id"])
            return
        if result.status == "completed":
            try:
                await self._handle_output(
                    job["id"], remote_url=result.remote_output_url,
                    b64=result.b64_output, actual=result.actual_settings,
                    model=result.model or job["model"],
                )
            except DownloadError as exc:
                job2 = self.db.get_job(job["id"])
                attempts = int(job2["attempts"])
                max_attempts = int(job2["max_attempts"] or 3)
                await self._handle_download_failure(job["id"], job2, exc, attempts, max_attempts)

    # -- batch controls ---------------------------------------------------
    def pause_batch(self, batch_id: int) -> None:
        self.db.update_batch_status(batch_id, "paused")
        logger.info("batch %s paused", batch_id)

    def resume_batch(self, batch_id: int) -> None:
        self.db.update_batch_status(batch_id, "running")
        logger.info("batch %s resumed", batch_id)

    def cancel_batch(self, batch_id: int) -> None:
        rows = self.db.mark_batch_cancelled_jobs(batch_id)
        for job in rows:
            provider = self.providers.get(job["provider"])
            if provider and (job["remote_task_id"] or job["remote_video_id"]):
                try:
                    task = asyncio.create_task(provider.cancel(job))
                    self._cancel_tasks.append(task)
                    task.add_done_callback(
                        lambda t: self._cancel_tasks.remove(t)
                    )
                except Exception:
                    logger.exception("cancel failed for job %s", job["id"])
        self.db.update_batch_status(batch_id, "cancelled")
        self.db.refresh_batch_counts(batch_id)
        self._sync_manifest(batch_id)
        logger.info("batch %s cancelled", batch_id)

    def retry_failed_batch(self, batch_id: int) -> int:
        n = self.db.retry_failed_jobs(batch_id)
        batch = self.db.get_batch(batch_id)
        if batch and batch["status"] != "running":
            self.db.update_batch_status(batch_id, "running")
        self._sync_manifest(batch_id)
        logger.info("batch %s: %d failed jobs re-queued", batch_id, n)
        return n

    def retry_job(self, job_id: int) -> None:
        self.db.reset_job_for_retry(job_id)
        batch = self.db.get_job(job_id)
        if batch:
            self.db.update_batch_status(batch["batch_id"], "running")
            self._sync_manifest(batch["batch_id"])
        logger.info("job %s re-queued", job_id)


def _iso_add_seconds(seconds: float) -> str:
    from datetime import datetime, timedelta

    return (
        datetime.now(UTC) + timedelta(seconds=seconds)
    ).isoformat(timespec="seconds")
