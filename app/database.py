"""SQLite persistence layer.

Schema is created and migrated automatically at startup. All writes happen
inside short-lived connections in WAL mode. Timestamps are ISO-8601 UTC
strings.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    total_jobs INTEGER NOT NULL DEFAULT 0,
    completed_jobs INTEGER NOT NULL DEFAULT 0,
    failed_jobs INTEGER NOT NULL DEFAULT 0,
    pricing_acknowledged INTEGER NOT NULL DEFAULT 0,
    fallback_provider TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    job_index INTEGER NOT NULL,
    type TEXT NOT NULL,
    prompt TEXT NOT NULL,
    negative_prompt TEXT,
    provider TEXT NOT NULL,
    model TEXT,
    fallback_provider TEXT,
    requested_settings TEXT,
    actual_settings TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    remote_task_id TEXT,
    remote_video_id TEXT,
    remote_output_url TEXT,
    local_output_path TEXT,
    error_type TEXT,
    error_message TEXT,
    retry_at TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_batch_index ON jobs(batch_id, job_index);

CREATE TABLE IF NOT EXISTS provider_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    timestamp TEXT NOT NULL,
    provider TEXT NOT NULL,
    event_type TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_job ON provider_events(job_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON provider_events(timestamp);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS provider_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    pricing_status TEXT NOT NULL DEFAULT 'free',
    last_verified TEXT,
    doc_label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    expected_price TEXT,
    notes TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_model ON provider_metadata(provider, model);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def iso_to_epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    def _run(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, tuple(params))
                conn.commit()
                return cur
            finally:
                conn.close()

    def _run_many(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(sql, rows)
                conn.commit()
            finally:
                conn.close()

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

    def _query_one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # -- settings -----------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        row = self._query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._run(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def all_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._query("SELECT key, value FROM settings")}

    # -- provider metadata --------------------------------------------
    def seed_provider_metadata(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            existing = self._query_one(
                "SELECT id FROM provider_metadata WHERE provider=? AND model=?",
                (row["provider"], row["model"]),
            )
            if existing:
                self._run(
                    "UPDATE provider_metadata SET pricing_status=?, doc_label=?, "
                    "enabled=?, expected_price=?, notes=? WHERE id=?",
                    (
                        row.get("pricing_status", "free"),
                        row.get("doc_label", ""),
                        1 if row.get("enabled", True) else 0,
                        row.get("expected_price", ""),
                        row.get("notes", ""),
                        existing["id"],
                    ),
                )
            else:
                self._run(
                    "INSERT INTO provider_metadata"
                    "(provider, model, pricing_status, last_verified, doc_label,"
                    " enabled, expected_price, notes) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        row["provider"],
                        row["model"],
                        row.get("pricing_status", "free"),
                        utcnow(),
                        row.get("doc_label", ""),
                        1 if row.get("enabled", True) else 0,
                        row.get("expected_price", ""),
                        row.get("notes", ""),
                    ),
                )

    def provider_metadata(self) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM provider_metadata ORDER BY provider, model")

    def touch_provider_verified(self, provider: str, model: str) -> None:
        self._run(
            "UPDATE provider_metadata SET last_verified=? WHERE provider=? AND model=?",
            (utcnow(), provider, model),
        )

    # -- batches --------------------------------------------------------
    def create_batch(
        self,
        name: str,
        media_type: str,
        fallback_provider: str | None,
        pricing_acknowledged: bool = False,
    ) -> int:
        now = utcnow()
        cur = self._run(
            "INSERT INTO batches(name, media_type, status, total_jobs,"
            " completed_jobs, failed_jobs, pricing_acknowledged,"
            " fallback_provider, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                name,
                media_type,
                "running",
                0,
                0,
                0,
                1 if pricing_acknowledged else 0,
                fallback_provider,
                now,
                now,
            ),
        )
        return cur.lastrowid

    def get_batch(self, batch_id: int) -> sqlite3.Row | None:
        return self._query_one("SELECT * FROM batches WHERE id=?", (batch_id,))

    def list_batches(self, limit: int = 200, offset: int = 0) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM batches ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    def count_batches(self) -> int:
        row = self._query_one("SELECT COUNT(*) AS n FROM batches")
        return int(row["n"]) if row else 0

    def update_batch_status(self, batch_id: int, status: str) -> None:
        self._run(
            "UPDATE batches SET status=?, updated_at=? WHERE id=?",
            (status, utcnow(), batch_id),
        )

    def set_batch_acknowledged(self, batch_id: int) -> None:
        self._run(
            "UPDATE batches SET pricing_acknowledged=1, updated_at=? WHERE id=?",
            (utcnow(), batch_id),
        )

    def refresh_batch_counts(self, batch_id: int) -> None:
        self._run(
            "UPDATE batches SET completed_jobs = "
            "(SELECT COUNT(*) FROM jobs WHERE batch_id=? AND status='completed'),"
            " failed_jobs = "
            "(SELECT COUNT(*) FROM jobs WHERE batch_id=? AND status='failed'),"
            " updated_at=? WHERE id=?",
            (batch_id, batch_id, utcnow(), batch_id),
        )

    def batch_counts(self, batch_id: int) -> tuple[int, int]:
        row = self._query_one(
            "SELECT "
            " (SELECT COUNT(*) FROM jobs WHERE batch_id=? AND status='completed') AS done,"
            " (SELECT COUNT(*) FROM jobs WHERE batch_id=? AND status='failed') AS failed",
            (batch_id, batch_id),
        )
        if not row:
            return 0, 0
        return int(row["done"]), int(row["failed"])

    # -- jobs ----------------------------------------------------------
    def create_jobs(self, batch_id: int, jobs: list[dict[str, Any]]) -> None:
        rows = []
        for job in jobs:
            rows.append(
                (
                    batch_id,
                    int(job["job_index"]),
                    job["type"],
                    job["prompt"],
                    job.get("negative_prompt") or None,
                    job["provider"],
                    job.get("model"),
                    job.get("fallback_provider"),
                    json.dumps(job.get("requested_settings") or {}, ensure_ascii=False),
                    None,
                    "pending",
                    0,
                    int(job.get("max_attempts", 3)),
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    utcnow(),
                    None,
                    None,
                    utcnow(),
                )
            )
        self._run_many(
            "INSERT INTO jobs(batch_id, job_index, type, prompt, negative_prompt,"
            " provider, model, fallback_provider, requested_settings, actual_settings,"
            " status, attempts, max_attempts, remote_task_id, remote_video_id,"
            " remote_output_url, local_output_path, error_type, error_message,"
            " retry_at, created_at, started_at, completed_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self._run(
            "UPDATE batches SET total_jobs = "
            "(SELECT COUNT(*) FROM jobs WHERE batch_id=?), updated_at=? WHERE id=?",
            (batch_id, utcnow(), batch_id),
        )

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        return self._query_one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def list_jobs(
        self, batch_id: int, limit: int = 100, offset: int = 0,
        status: str | None = None,
    ) -> list[sqlite3.Row]:
        if status:
            return self._query(
                "SELECT * FROM jobs WHERE batch_id=? AND status=? "
                "ORDER BY job_index LIMIT ? OFFSET ?",
                (batch_id, status, limit, offset),
            )
        return self._query(
            "SELECT * FROM jobs WHERE batch_id=? ORDER BY job_index LIMIT ? OFFSET ?",
            (batch_id, limit, offset),
        )

    def count_jobs(self, batch_id: int) -> int:
        row = self._query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE batch_id=?", (batch_id,)
        )
        return int(row["n"]) if row else 0

    def count_jobs_by_status(self, batch_id: int, status: str) -> int:
        row = self._query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE batch_id=? AND status=?",
            (batch_id, status),
        )
        return int(row["n"]) if row else 0

    def job_counts_all(self) -> dict[str, int]:
        rows = self._query("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        return {r["status"]: int(r["n"]) for r in rows}

    # -- job transitions (used by the queue manager) -------------------
    def claim_job(self, job_id: int) -> bool:
        """Atomically claim a pending job. Returns False if already claimed."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE jobs SET status='processing', attempts=attempts+1,"
                    " started_at=?, updated_at=? WHERE id=? AND status='pending'",
                    (utcnow(), utcnow(), job_id),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    def schedule_retry(
        self, job_id: int, retry_at: str, error_type: str, error_message: str,
    ) -> None:
        self._run(
            "UPDATE jobs SET status='pending', retry_at=?, error_type=?,"
            " error_message=?, updated_at=? WHERE id=?",
            (retry_at, error_type, error_message[:2000], utcnow(), job_id),
        )

    def fail_job(self, job_id: int, error_type: str, error_message: str) -> None:
        self._run(
            "UPDATE jobs SET status='failed', error_type=?, error_message=?,"
            " completed_at=?, updated_at=? WHERE id=?",
            (error_type, error_message[:2000], utcnow(), utcnow(), job_id),
        )

    def set_job_submitted_remote(
        self, job_id: int, remote_task_id: str | None, remote_video_id: str | None,
        actual_settings: dict | None = None, model: str | None = None,
    ) -> None:
        self._run(
            "UPDATE jobs SET status='queued_remote', remote_task_id=?,"
            " remote_video_id=?, actual_settings=?, model=COALESCE(?, model),"
            " error_type=NULL, error_message=NULL, updated_at=?"
            " WHERE id=?",
            (
                remote_task_id, remote_video_id,
                json.dumps(actual_settings or {}, ensure_ascii=False),
                model, utcnow(), job_id,
            ),
        )

    def set_job_polling(self, job_id: int, progress: int | None = None) -> None:
        self._run(
            "UPDATE jobs SET status='polling', updated_at=? WHERE id=?",
            (utcnow(), job_id),
        )

    def update_polling_actual(
        self, job_id: int, status: str, actual_settings: dict | None,
        progress: int | None = None,
    ) -> None:
        self._run(
            "UPDATE jobs SET status=?, actual_settings=?, updated_at=? WHERE id=?",
            (status, json.dumps(actual_settings or {}, ensure_ascii=False),
             utcnow(), job_id),
        )

    def set_job_progress(self, job_id: int, progress: int | None) -> None:
        if progress is None:
            return
        self._run(
            "UPDATE jobs SET actual_settings=COALESCE(actual_settings,'{}'),"
            " updated_at=? WHERE id=?",
            (utcnow(), job_id),
        )

    def finalize_job(
        self, job_id: int, local_path: str, actual_settings: dict,
        remote_output_url: str | None, model: str | None,
    ) -> None:
        self._run(
            "UPDATE jobs SET status='completed', local_output_path=?,"
            " actual_settings=?, remote_output_url=?, model=?,"
            " error_type=NULL, error_message=NULL, completed_at=?, updated_at=?"
            " WHERE id=?",
            (
                local_path,
                json.dumps(actual_settings, ensure_ascii=False),
                remote_output_url,
                model,
                utcnow(),
                utcnow(),
                job_id,
            ),
        )

    def cancel_job(self, job_id: int) -> None:
        self._run(
            "UPDATE jobs SET status='cancelled', error_message=?,"
            " completed_at=?, updated_at=? WHERE id=?",
            ("cancelled locally; remote computation may still finish",
             utcnow(), utcnow(), job_id),
        )

    def reset_job_to_pending(self, job_id: int) -> None:
        """Return a claimed job to pending (e.g. batch paused mid-claim)."""
        self._run(
            "UPDATE jobs SET status='pending', updated_at=? WHERE id=?",
            (utcnow(), job_id),
        )

    def reset_job_for_retry(self, job_id: int) -> None:
        self._run(
            "UPDATE jobs SET status='pending', attempts=0, remote_task_id=NULL,"
            " remote_video_id=NULL, remote_output_url=NULL, local_output_path=NULL,"
            " error_type=NULL, error_message=NULL, retry_at=NULL, completed_at=NULL,"
            " updated_at=? WHERE id=?",
            (utcnow(), job_id),
        )

    def switch_fallback(self, job_id: int, provider: str, model: str | None) -> None:
        """Switch a job to its fallback provider once (no loops)."""
        job = self.get_job(job_id)
        if not job:
            return
        req = json.loads(job["requested_settings"] or "{}")
        req["_fallback_used"] = True
        self._run(
            "UPDATE jobs SET provider=?, model=?, attempts=0, status='pending',"
            " retry_at=NULL, error_type=NULL, error_message=NULL,"
            " requested_settings=?, updated_at=? WHERE id=?",
            (provider, model, json.dumps(req, ensure_ascii=False), utcnow(), job_id),
        )

    def mark_batch_cancelled_jobs(self, batch_id: int) -> list[sqlite3.Row]:
        """Cancel all non-terminal jobs of a batch and return the affected rows."""
        rows = self._query(
            "SELECT * FROM jobs WHERE batch_id=? AND status IN"
            " ('pending','processing','queued_remote','polling')",
            (batch_id,),
        )
        self._run(
            "UPDATE jobs SET status='cancelled', completed_at=?, updated_at=?"
            " WHERE batch_id=? AND status IN"
            " ('pending','processing','queued_remote','polling')",
            (utcnow(), utcnow(), batch_id),
        )
        return rows

    def retry_failed_jobs(self, batch_id: int) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE jobs SET status='pending', attempts=0,"
                    " error_type=NULL, error_message=NULL, retry_at=NULL,"
                    " completed_at=NULL, started_at=NULL, updated_at=? WHERE batch_id=?"
                    " AND status='failed'",
                    (utcnow(), batch_id),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def duplicate_batch(self, batch_id: int) -> int:
        """Deep-copy a batch and its jobs into a fresh batch."""
        src = self.get_batch(batch_id)
        if not src:
            raise ValueError("batch not found")
        new_id = self.create_batch(
            name=src["name"] + " (copy)",
            media_type=src["media_type"],
            fallback_provider=src["fallback_provider"],
            pricing_acknowledged=bool(src["pricing_acknowledged"]),
        )
        jobs = self._query("SELECT * FROM jobs WHERE batch_id=?", (batch_id,))
        new_jobs = []
        for job in jobs:
            new_jobs.append(
                {
                    "job_index": job["job_index"],
                    "type": job["type"],
                    "prompt": job["prompt"],
                    "negative_prompt": job["negative_prompt"],
                    "provider": job["provider"],
                    "model": job["model"],
                    "fallback_provider": job["fallback_provider"],
                    "requested_settings": json.loads(job["requested_settings"] or "{}"),
                    "max_attempts": job["max_attempts"],
                }
            )
        self.create_jobs(new_id, new_jobs)
        return new_id

    # -- queue scans -----------------------------------------------------
    def pick_next_pending_job(self, now_iso: str) -> sqlite3.Row | None:
        return self._query_one(
            "SELECT j.* FROM jobs j JOIN batches b ON b.id=j.batch_id"
            " WHERE j.status='pending' AND b.status='running'"
            " AND (j.retry_at IS NULL OR j.retry_at <= ?)"
            " ORDER BY b.id, j.job_index LIMIT 1",
            (now_iso,),
        )

    def pick_remote_jobs(self) -> list[sqlite3.Row]:
        return self._query(
            "SELECT j.* FROM jobs j JOIN batches b ON b.id=j.batch_id"
            " WHERE j.status IN ('queued_remote','polling') AND b.status='running'"
            " ORDER BY b.id, j.job_index"
        )

    # -- provider events -------------------------------------------------
    def log_event(
        self, job_id: int | None, provider: str, event_type: str,
        metadata: dict | None = None,
    ) -> None:
        self._run(
            "INSERT INTO provider_events(job_id, timestamp, provider, event_type,"
            " metadata) VALUES(?,?,?,?,?)",
            (job_id, utcnow(), provider, event_type,
             json.dumps(metadata or {}, ensure_ascii=False, default=str)[:4000]),
        )

    def recent_events(self, limit: int = 200) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM provider_events ORDER BY id DESC LIMIT ?", (limit,)
        )

    def events_for_job(self, job_id: int, limit: int = 100) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM provider_events WHERE job_id=? ORDER BY id DESC LIMIT ?",
            (job_id, limit),
        )

    def reconcile_stale_processing(self) -> int:
        """On startup: move stale local processing states back to pending.

        Returns the number of jobs reconciled. Remote jobs (queued_remote /
        polling) are left alone so polling resumes after restart.
        """
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE jobs SET status='pending', retry_at=NULL, updated_at=?"
                    " WHERE status='processing'",
                    (utcnow(),),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def reset_stuck_polling_to_queued(self) -> int:
        """Move polling jobs back to queued_remote so the poller re-polls."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE jobs SET status='queued_remote', updated_at=? "
                    "WHERE status='polling'",
                    (utcnow(),),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def find_part_files(self, output_dir: Path) -> list[Path]:
        parts = []
        for path in output_dir.rglob("*.part"):
            parts.append(path)
        return parts
