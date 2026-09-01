"""Batch ZIP export with traversal-safe member names."""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from app.manifest import batch_output_dir
from app.security import zip_arcname_is_safe


def _add_to_zip(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
    if not zip_arcname_is_safe(arcname):
        raise ValueError(f"refusing unsafe zip member name: {arcname!r}")
    zf.write(src, arcname)


def create_batch_zip(
    batch_id: int,
    *,
    include_failed: bool = False,
    include_metadata: bool = True,
    out_root: Path | None = None,
    completed_files: set[str] | None = None,
) -> Path:
    """Create a ZIP of a batch's outputs and return its path.

    The zip is written to a temporary location (never inside the batch dir) so
    re-exports don't nest zips. Videos are stored uncompressed (ZIP_STORED) to
    keep CPU and RAM usage low on modest hardware.

    When ``completed_files`` is provided and ``include_failed`` is False, only
    files belonging to completed jobs (plus manifests) are archived.
    """
    out_dir = batch_output_dir(batch_id, out_root)
    if not out_dir.exists():
        raise FileNotFoundError(f"batch {batch_id} has no output directory")

    tmp = Path(tempfile.gettempdir()) / "freebatch-zips"
    tmp.mkdir(parents=True, exist_ok=True)
    zip_path = tmp / f"batch_{batch_id}.zip"

    names: list[str] = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for child in sorted(out_dir.iterdir()):
            if not child.is_file():
                continue
            if child.suffix.lower() in (".part", ".zip"):
                continue
            if child.name.startswith("manifest.") and not include_metadata:
                continue
            if (
                not include_failed
                and completed_files is not None
                and child.name not in completed_files
                and not child.name.startswith("manifest.")
            ):
                continue
            _add_to_zip(zf, child, f"batch_{batch_id}/{child.name}")
            names.append(f"batch_{batch_id}/{child.name}")

    with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_STORED) as zf:
        index = {
            "batch_id": batch_id,
            "created": batch_id,
            "members": names,
        }
        _add_to_zip(
            zf,
            _write_string(tmp / f"batch_{batch_id}_index.json", json.dumps(index)),
            f"batch_{batch_id}/_index.json",
        )
    return zip_path


def _write_string(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def validate_zip_members(zip_path: Path, base_dir: Path) -> None:
    """Verify every member in a zip is safe relative to base_dir."""
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if not zip_arcname_is_safe(info.filename):
                raise ValueError(f"unsafe zip member: {info.filename!r}")
            target = (base_dir / info.filename).resolve()
            if not str(target).startswith(str(base_dir.resolve())):
                raise ValueError(f"zip member escapes base dir: {info.filename!r}")
