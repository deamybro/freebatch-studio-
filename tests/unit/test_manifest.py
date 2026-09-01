"""Unit tests for manifest export and ZIP creation."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from app.database import Database
from app.manifest import batch_output_dir, write_manifest
from app.zipper import create_batch_zip, validate_zip_members


def _make_batch(db: Database, settings, tmp_path) -> int:
    settings.output_dir_path().mkdir(parents=True, exist_ok=True)
    bid = db.create_batch("Batch A", "image", "ai_horde")
    db.create_jobs(
        bid,
        [
            {
                "job_index": 0,
                "type": "text_to_image",
                "prompt": "red circle",
                "provider": "agnes_image",
                "requested_settings": {"size": "1K", "ratio": "1:1"},
            }
        ],
    )
    return bid


def _complete_job(db: Database, bid: int, out_file: Path):
    job = db.list_jobs(bid)[0]
    rel = str(out_file.relative_to(db_path_parent(out_file))) if False else str(out_file)
    db.finalize_job(job["id"], rel, {"size": "1K"}, "https://cdn/x.png", "m")
    return job["id"]


def db_path_parent(path: Path) -> Path:
    return Path.cwd()


class TestWriteManifest:
    def test_creates_csv_and_json(self, db, settings, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        bid = _make_batch(db, settings, tmp_path)

        csv_path, json_path = write_manifest(db, bid)
        assert csv_path.exists()
        assert json_path.exists()
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        assert doc["name"] == "Batch A"
        assert doc["media_type"] == "image"
        assert doc["total_jobs"] == 1
        assert doc["jobs"][0]["prompt"] == "red circle"
        # manifest.csv written with utf-8-sig BOM
        raw = csv_path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")

    def test_missing_batch_raises(self, db, settings, monkeypatch):
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        with pytest.raises(ValueError):
            write_manifest(db, 999)


class TestBatchOutputDir:
    def test_returns_under_output_root(self, settings, monkeypatch):
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        d = batch_output_dir(7)
        assert d == settings.output_dir_path() / "7"


class TestCreateBatchZip:
    def test_zip_created_with_outputs(self, db, settings, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        bid = _make_batch(db, settings, tmp_path)
        out_dir = settings.output_dir_path() / str(bid)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "0001_red_circle.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        (out_dir / "0001_red_circle.png.part").write_bytes(b"junk")

        zip_path = create_batch_zip(bid, include_metadata=True)
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert any(n.endswith("0001_red_circle.png") for n in names)
            assert not any(n.endswith(".part") for n in names)
            assert any(n.endswith("_index.json") for n in names)

    def test_zip_missing_batch_dir(self, db, settings, monkeypatch, tmp_path):
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        with pytest.raises(FileNotFoundError):
            create_batch_zip(999)

    def test_validate_zip_members_accepts_good(self, db, settings, monkeypatch, tmp_path):
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        bid = _make_batch(db, settings, tmp_path)
        out_dir = settings.output_dir_path() / str(bid)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "0001_x.png").write_bytes(b"\x89PNG")
        zip_path = create_batch_zip(bid, include_metadata=False)
        base = settings.output_dir_path()
        validate_zip_members(zip_path, base)  # must not raise

    def test_validate_zip_members_rejects_traversal(self, tmp_path):
        zpath = tmp_path / "evil.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("../escape.txt", "evil")
        with pytest.raises(ValueError):
            validate_zip_members(zpath, tmp_path)
