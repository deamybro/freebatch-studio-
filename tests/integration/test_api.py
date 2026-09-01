"""Integration tests for the FastAPI application (hermetic; no network).

We point every setting at temp directories BEFORE importing ``app.main`` so the
module-level ``app_state`` is fully isolated. The queue worker/poller loops are
NOT started; a real QueueManager is built with fake providers so batch control
endpoints exercise real queue code without touching the network.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx
import pytest

_TMP = Path(tempfile.mkdtemp(prefix="freebatch-it-"))
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["OUTPUT_DIR"] = str(_TMP / "outputs")
os.environ["LOGS_DIR"] = str(_TMP / "logs")
os.environ["DB_PATH"] = str(_TMP / "app.db")
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["FREE_ONLY_MODE"] = "true"
os.environ["AGNES_API_KEY"] = "itest-key"
os.environ["AI_HORDE_API_KEY"] = "0000000000"

from app.main import app, app_state  # noqa: E402
from app.providers.base import HealthStatus, SubmitResult  # noqa: E402
from app.providers.registry import seed_metadata  # noqa: E402


class FakeProvider:
    name = "fake"
    display_name = "Fake"
    media_types = ("image",)
    supports_async = False

    def __init__(self, name, display_name, media_types=("image",)):
        self.name = name
        self.display_name = display_name
        self.media_types = media_types

    def is_configured(self):
        return True

    def capabilities(self):
        return {"name": self.name, "job_types": ["text_to_image"]}

    async def healthcheck(self):
        return HealthStatus(state="reachable", detail="fake", reachable=True,
                            authenticated=True, checked_at="now")

    async def verify(self):
        return HealthStatus(state="authenticated", reachable=True, authenticated=True)

    async def submit(self, job):
        return SubmitResult(status="processing")


class FakeQueue:
    """Real QueueManager with fake providers (no loops started)."""

    def __init__(self, providers):
        from app.queue_manager import QueueManager

        self._inner = QueueManager(
            app_state.db, app_state.runtime, app_state.rate_limiter,
            providers, app_state.client,
        )

    def pause_batch(self, bid):
        self._inner.pause_batch(bid)

    def resume_batch(self, bid):
        self._inner.resume_batch(bid)

    def cancel_batch(self, bid):
        self._inner.cancel_batch(bid)

    def retry_failed_batch(self, bid):
        return self._inner.retry_failed_batch(bid)

    def retry_job(self, jid):
        self._inner.retry_job(jid)


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    app_state.csrf.check_env_setup()
    app_state.client = httpx.AsyncClient()
    providers = {
        "agnes_image": FakeProvider("agnes_image", "Agnes Image"),
        "agnes_video": FakeProvider("agnes_video", "Agnes Video", ("video",)),
        "ai_horde": FakeProvider("ai_horde", "AI Horde"),
        "wangp": FakeProvider("wangp", "WanGP"),
        "comfyui": FakeProvider("comfyui", "ComfyUI"),
    }
    app_state.providers = providers
    app_state.queue = FakeQueue(providers)
    app_state.health_cache.clear()
    seed_metadata(app_state.db)
    return c


async def _csrf(c):
    r = await c.get("/api/csrf")
    return r.json()["token"]


async def _headers(c):
    return {"x-csrf-token": await _csrf(c), "content-type": "application/json"}


def _image_jobs():
    return [
        {
            "type": "text_to_image",
            "prompt": "red circle",
            "provider": "agnes_image",
            "model": "agnes-image-2.1-flash",
            "requested_settings": {"size": "1K", "ratio": "1:1"},
        }
    ]


class TestCsrf:
    async def test_get_csrf_token(self, client):
        r = await client.get("/api/csrf")
        assert r.status_code == 200
        assert len(r.json()["token"]) >= 20

    async def test_mutating_without_token_forbidden(self, client):
        r = await client.post("/api/batches", json={"name": "x", "media_type": "image",
                                                    "jobs": _image_jobs()})
        assert r.status_code == 403
        assert "CSRF" in r.json()["detail"]


class TestParseAndCreate:
    async def test_parse_text(self, client):
        r = await client.post("/api/batches/parse",
                              headers=await _headers(client),
                              json={"media_type": "image", "text": "a\nb\n"})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        assert body["jobs"][0]["provider"] == "agnes_image"

    async def test_parse_csv(self, client):
        r = await client.post("/api/batches/parse",
                              headers=await _headers(client),
                              json={"media_type": "video", "csv": "prompt\nclip\n"})
        assert r.status_code == 200
        assert r.json()["count"] == 1
        assert r.json()["jobs"][0]["type"] == "text_to_video"

    async def test_parse_bad_media_type(self, client):
        r = await client.post("/api/batches/parse",
                              headers=await _headers(client),
                              json={"media_type": "audio"})
        assert r.status_code == 400

    async def test_create_batch(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "Test", "media_type": "image",
                                    "jobs": _image_jobs()})
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Test"
        assert body["total_jobs"] == 1

    async def test_create_batch_no_jobs(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "x", "media_type": "image", "jobs": []})
        assert r.status_code == 400

    async def test_create_batch_unknown_provider_blocked(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "x", "media_type": "image",
                                    "jobs": [{"type": "text_to_image", "prompt": "p",
                                              "provider": "not_real",
                                              "requested_settings": {}}]})
        assert r.status_code == 400

    async def test_create_batch_large_stale_requires_ack(self, client):
        # Force stale pricing so a large batch needs acknowledgement.
        from datetime import UTC, datetime, timedelta

        old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        for row in app_state.db.provider_metadata():
            app_state.db._run(
                "UPDATE provider_metadata SET last_verified=? WHERE id=?",
                (old, row["id"]),
            )
        jobs = _image_jobs() * 60
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "big", "media_type": "image", "jobs": jobs})
        assert r.status_code == 400
        assert "Acknowledge" in r.json()["detail"]
        # with acknowledgement it succeeds
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "big", "media_type": "image", "jobs": jobs,
                                    "pricing_acknowledged": True})
        assert r.status_code == 201


class TestBatches:
    async def test_list_batches(self, client):
        r = await client.get("/api/batches")
        assert r.status_code == 200
        assert "batches" in r.json()

    async def test_get_batch_and_jobs(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        r = await client.get(f"/api/batches/{bid}")
        assert r.status_code == 200
        r = await client.get(f"/api/batches/{bid}/jobs")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    async def test_get_missing_batch(self, client):
        assert (await client.get("/api/batches/999")).status_code == 404
        assert (await client.get("/api/batches/999/jobs")).status_code == 404

    async def test_pause_resume_cancel(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        h = await _headers(client)
        assert (await client.post(f"/api/batches/{bid}/pause", headers=h)).status_code == 200
        assert (await client.get(f"/api/batches/{bid}")).json()["status"] == "paused"
        assert (await client.post(f"/api/batches/{bid}/resume", headers=h)).status_code == 200
        assert (await client.post(f"/api/batches/{bid}/cancel", headers=h)).status_code == 200
        assert (await client.get(f"/api/batches/{bid}")).json()["status"] == "cancelled"

    async def test_retry_failed(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        job = (await client.get(f"/api/batches/{bid}/jobs")).json()["jobs"][0]
        app_state.db.fail_job(job["id"], "auth", "x")
        r = await client.post(f"/api/batches/{bid}/retry-failed",
                              headers=await _headers(client))
        assert r.status_code == 200
        assert r.json()["requeued"] == 1

    async def test_duplicate(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        r = await client.post(f"/api/batches/{bid}/duplicate",
                              headers=await _headers(client))
        assert r.status_code == 200
        assert r.json()["new_batch_id"] != bid

    async def test_manifest_export(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        r = await client.get(f"/api/batches/{bid}/manifest")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        r = await client.get(f"/api/batches/{bid}/manifest.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]

    async def test_zip_export(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        out = app_state.runtime.settings.output_dir_path() / str(bid)
        out.mkdir(parents=True, exist_ok=True)
        (out / "0001_red_circle.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        r = await client.get(f"/api/batches/{bid}/zip")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/zip")

    async def test_zip_export_missing_dir(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        assert (await client.get(f"/api/batches/{bid}/zip")).status_code == 404

    async def test_control_missing_batch(self, client):
        h = await _headers(client)
        assert (await client.post("/api/batches/999/pause", headers=h)).status_code == 404


class TestJobs:
    async def test_retry_job(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        job = (await client.get(f"/api/batches/{bid}/jobs")).json()["jobs"][0]
        r = await client.post(f"/api/jobs/{job['id']}/retry",
                              headers=await _headers(client))
        assert r.status_code == 200

    async def test_retry_missing_job(self, client):
        assert (await client.post("/api/jobs/999/retry",
                                  headers=await _headers(client))).status_code == 404

    async def test_job_file(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        job = (await client.get(f"/api/batches/{bid}/jobs")).json()["jobs"][0]
        out = app_state.runtime.settings.output_dir_path() / str(bid)
        out.mkdir(parents=True, exist_ok=True)
        f = out / "0001_red_circle.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        app_state.db.finalize_job(job["id"], str(f.relative_to(
            app_state.runtime.settings.output_dir_path())), {}, None, "m")
        r = await client.get(f"/api/jobs/{job['id']}/file")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")

    async def test_job_file_missing(self, client):
        assert (await client.get("/api/jobs/999/file")).status_code == 404

    async def test_job_events(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        job = (await client.get(f"/api/batches/{bid}/jobs")).json()["jobs"][0]
        app_state.db.log_event(job["id"], "agnes_image", "submit")
        r = await client.get(f"/api/jobs/{job['id']}/events")
        assert r.status_code == 200
        assert len(r.json()) == 1


class TestProvidersApi:
    async def test_list_providers(self, client):
        r = await client.get("/api/providers")
        assert r.status_code == 200
        body = r.json()
        assert body["free_only_mode"] is True
        names = {p["name"] for p in body["providers"]}
        assert "agnes_image" in names
        assert "wangp" in names
        wangp = next(p for p in body["providers"] if p["name"] == "wangp")
        assert wangp["enabled"] is False

    async def test_provider_health(self, client):
        r = await client.post("/api/providers/agnes_image/health",
                              headers=await _headers(client))
        assert r.status_code == 200
        assert r.json()["reachable"] is True

    async def test_provider_health_unknown(self, client):
        r = await client.post("/api/providers/nope/health",
                              headers=await _headers(client))
        assert r.status_code == 404

    async def test_provider_verify_future_blocked(self, client):
        r = await client.post("/api/providers/wangp/verify",
                              headers=await _headers(client))
        assert r.status_code == 400

    async def test_provider_verify_ok(self, client):
        r = await client.post("/api/providers/agnes_image/verify",
                              headers=await _headers(client))
        assert r.status_code == 200
        assert r.json()["authenticated"] is True

    async def test_provider_models_unsupported(self, client):
        r = await client.get("/api/providers/agnes_image/models")
        assert r.status_code == 400

    async def test_provider_models_unknown(self, client):
        assert (await client.get("/api/providers/nope/models")).status_code == 404


class TestSettingsApi:
    async def test_get_settings(self, client, monkeypatch):
        monkeypatch.setenv("AGNES_API_KEY", "itest-key")
        r = await client.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["secret_status"]["AGNES_API_KEY"] != "(not configured)"

    async def test_update_settings(self, client):
        r = await client.put("/api/settings",
                             headers=await _headers(client),
                             json={"values": {"max_attempts": 5}})
        assert r.status_code == 200
        assert r.json()["max_attempts"] == 5

    async def test_update_settings_unknown_key(self, client):
        r = await client.put("/api/settings",
                             headers=await _headers(client),
                             json={"values": {"hacker_key": 1}})
        assert r.status_code == 400


class TestLogs:
    async def test_get_logs(self, client):
        r = await client.get("/api/logs")
        assert r.status_code == 200
        assert "lines" in r.json()


class TestValidateUrl:
    async def test_public_url_ok(self, client):
        r = await client.post("/api/validate-url",
                              headers=await _headers(client),
                              json={"url": "https://example.com/a.png"})
        assert r.json()["ok"] is True

    async def test_private_url_rejected(self, client):
        r = await client.post("/api/validate-url",
                              headers=await _headers(client),
                              json={"url": "https://127.0.0.1/a.png"})
        assert r.json()["ok"] is False


class TestDashboard:
    async def test_dashboard(self, client):
        r = await client.get("/api/dashboard")
        assert r.status_code == 200
        body = r.json()
        assert "total_batches" in body
        assert body["free_only_mode"] is True


class TestPages:
    async def test_index(self, client):
        r = await client.get("/")
        assert r.status_code == 200
        assert "FreeBatch" in r.text

    async def test_new_batch_pages(self, client):
        assert (await client.get("/batches/new-image")).status_code == 200
        assert (await client.get("/batches/new-video")).status_code == 200

    async def test_batches_page(self, client):
        assert (await client.get("/batches")).status_code == 200

    async def test_batch_detail(self, client):
        r = await client.post("/api/batches",
                              headers=await _headers(client),
                              json={"name": "T", "media_type": "image",
                                    "jobs": _image_jobs()})
        bid = r.json()["id"]
        assert (await client.get(f"/batches/{bid}")).status_code == 200
        assert (await client.get("/batches/999")).status_code == 404

    async def test_providers_settings_logs_pages(self, client):
        assert (await client.get("/providers")).status_code == 200
        assert (await client.get("/settings")).status_code == 200
        assert (await client.get("/logs")).status_code == 200

    async def test_static_css(self, client):
        r = await client.get("/static/css/style.css")
        assert r.status_code == 200


class TestExampleCsv:
    async def test_image_example(self, client):
        r = await client.get("/api/examples/image.csv")
        assert r.status_code == 200
        assert "prompt" in r.text

    async def test_unknown_example(self, client):
        assert (await client.get("/api/examples/bogus.csv")).status_code == 404
