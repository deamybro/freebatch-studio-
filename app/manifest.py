"""Manifest export (manifest.csv / manifest.json) per completed batch."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from app.database import Database


def _job_to_manifest_row(db: Database, job) -> dict:
    req = _json_or_empty(job["requested_settings"])
    act = _json_or_empty(job["actual_settings"])
    return {
        "job_index": job["job_index"],
        "type": job["type"],
        "prompt": job["prompt"],
        "negative_prompt": job["negative_prompt"] or "",
        "provider": job["provider"],
        "model": job["model"] or "",
        "status": job["status"],
        "attempts": job["attempts"],
        "requested_settings": json.dumps(req, ensure_ascii=False),
        "actual_settings": json.dumps(act, ensure_ascii=False),
        "remote_output_url": job["remote_output_url"] or "",
        "local_output_path": job["local_output_path"] or "",
        "remote_task_id": job["remote_task_id"] or "",
        "remote_video_id": job["remote_video_id"] or "",
        "error_type": job["error_type"] or "",
        "error_message": job["error_message"] or "",
        "created_at": job["created_at"] or "",
        "started_at": job["started_at"] or "",
        "completed_at": job["completed_at"] or "",
    }


def _json_or_empty(value) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {}


CSV_FIELDS = [
    "job_index", "type", "prompt", "negative_prompt", "provider", "model",
    "status", "attempts", "requested_settings", "actual_settings",
    "remote_output_url", "local_output_path", "remote_task_id",
    "remote_video_id", "error_type", "error_message",
    "created_at", "started_at", "completed_at",
]


def write_manifest(
    db: Database, batch_id: int, out_root: Path | None = None,
) -> tuple[Path, Path]:
    """Write manifest.csv and manifest.json into the batch output directory."""
    batch = db.get_batch(batch_id)
    if not batch:
        raise ValueError("batch not found")
    jobs = db.list_jobs(batch_id, limit=100000)
    out_dir = _batch_dir(db, batch, out_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = [_job_to_manifest_row(db, j) for j in jobs]
    with open(out_dir / "manifest.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    doc = {
        "batch_id": batch_id,
        "name": batch["name"],
        "media_type": batch["media_type"],
        "status": batch["status"],
        "created_at": batch["created_at"],
        "updated_at": batch["updated_at"],
        "total_jobs": len(rows),
        "completed_jobs": sum(1 for r in rows if r["status"] == "completed"),
        "failed_jobs": sum(1 for r in rows if r["status"] == "failed"),
        "jobs": rows,
    }
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    return out_dir / "manifest.csv", out_dir / "manifest.json"


def _default_out_root() -> Path:
    from app.config import get_settings

    return get_settings().output_dir_path()


def _batch_dir(db: Database, batch, out_root: Path | None = None) -> Path:
    """Resolve the canonical output directory for a batch.

    Batches live under the output root /<batch_id>/. The batch table does not
    store a directory column; the layout is derived from the id (stable across
    restarts). ``out_root`` defaults to the env-configured output directory.
    """
    root = out_root or _default_out_root()
    return root / str(batch["id"])


def batch_output_dir(batch_id: int, out_root: Path | None = None) -> Path:
    root = out_root or _default_out_root()
    return root / str(batch_id)
