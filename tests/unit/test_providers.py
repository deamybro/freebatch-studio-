"""Unit tests for provider implementations (Agnes Image, Agnes Video, AI Horde)."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx
from app.providers.agnes_image import (
    BASE_URL as AGNES_BASE,
)
from app.providers.agnes_image import (
    ENDPOINT as AGNES_ENDPOINT,
)
from app.providers.agnes_image import (
    AgnesImageProvider,
)
from app.providers.agnes_video import (
    CREATE_ENDPOINT,
    RESULT_ENDPOINT,
    AgnesVideoProvider,
    clamp_frames,
)
from app.providers.ai_horde import BASE_URL as HORDE_BASE
from app.providers.ai_horde import AIHordeProvider
from app.providers.base import (
    ProviderError,
    backoff_seconds,
    classify_http_error,
)
from app.rate_limiter import RateLimiter
from app.security import CsrfProtector

MONKEY_KEY = "test-agnes-key-123456"


def _rl() -> RateLimiter:
    rl = RateLimiter()
    rl.load_rates(None, {"default": 100000})
    return rl


class _FakeSettings:
    def get(self, key, default=None):
        return default


@pytest.fixture
def agnes_settings(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", MONKEY_KEY)
    return _FakeSettings()


def _mk_provider(provider_cls, client, settings, rl, db):
    if rl is None:
        rl = _rl()
    return provider_cls(client, settings, rl, db)


def _image_job(**overrides):
    job = {
        "id": 1,
        "type": "text_to_image",
        "prompt": "a red circle",
        "provider": "agnes_image",
        "model": None,
        "requested_settings": {"size": "1K", "ratio": "1:1"},
    }
    job.update(overrides)
    return job


def _video_job(**overrides):
    job = {
        "id": 2,
        "type": "text_to_video",
        "prompt": "a cat walking",
        "provider": "agnes_video",
        "model": None,
        "requested_settings": {"num_frames": 121, "fps": 24, "duration": "5s"},
    }
    job.update(overrides)
    return job


class TestClampFrames:
    def test_snap_to_8n1(self):
        assert clamp_frames(None) == 121
        assert clamp_frames(80) == 81
        assert clamp_frames(100) == 97 or clamp_frames(100) in (97, 105)
        assert clamp_frames(500) == 441
        assert clamp_frames(0) == 1
        assert clamp_frames(81) == 81


class TestAgnesImageProvider:
    def test_is_configured_requires_key(self, agnes_settings, monkeypatch):
        monkeypatch.delenv("AGNES_API_KEY", raising=False)
        monkeypatch.setattr(
            "app.providers.agnes_image.get_agnes_api_key", lambda: ""
        )
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        assert p.is_configured() is False

    def test_capabilities(self, agnes_settings):
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        caps = p.capabilities()
        assert "text_to_image" in caps["job_types"]
        assert "1K" in caps["sizes"]
        assert caps["supports_base64"] is True

    def test_capabilities_serialize_json_safe(self, agnes_settings):
        # Regression: capabilities() exposes AGNES_IMAGE_DIMENSIONS, a dict keyed
        # by (ratio, tier) tuples. The /api/providers route must JSON-encode it
        # without error (FastAPI cannot serialize tuple dict keys).
        import json

        from app.security import json_safe

        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        safe = json_safe(p.capabilities())
        json.dumps(safe)  # must not raise on tuple-keyed dimensions
        assert "dimensions" in safe
        # tuple keys must have become strings, not lists
        assert all(isinstance(k, str) for k in safe["dimensions"])

    async def test_validate_job_ok(self, agnes_settings):
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        await p.validate_job(_image_job())

    async def test_validate_job_bad_size(self, agnes_settings):
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        with pytest.raises(ProviderError):
            await p.validate_job(_image_job(requested_settings={"size": "9K"}))

    async def test_validate_job_bad_ratio(self, agnes_settings):
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        with pytest.raises(ProviderError):
            await p.validate_job(_image_job(requested_settings={"ratio": "9:9"}))

    async def test_validate_job_i2i_requires_image(self, agnes_settings):
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        with pytest.raises(ProviderError):
            await p.validate_job(
                _image_job(type="image_to_image", requested_settings={})
            )

    @respx.mock
    async def test_submit_url(self, agnes_settings, db):
        respx.post(f"{AGNES_BASE}{AGNES_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"data": [{"url": "https://cdn/x.png"}]})
        )
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        res = await p.submit(_image_job())
        assert res.status == "processing"
        assert res.remote_output_url == "https://cdn/x.png"
        assert res.actual_settings["size"] == "1K"

    @respx.mock
    async def test_submit_b64(self, agnes_settings, db):
        payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8).decode()
        respx.post(f"{AGNES_BASE}{AGNES_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"data": [{"b64_json": payload}]})
        )
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        res = await p.submit(_image_job(requested_settings={"output_format": "b64_json"}))
        assert res.b64_output == payload

    @respx.mock
    async def test_submit_429_retryable(self, agnes_settings, db):
        respx.post(f"{AGNES_BASE}{AGNES_ENDPOINT}").mock(
            return_value=httpx.Response(429, headers={"retry-after": "5"}, text="slow down")
        )
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        with pytest.raises(ProviderError) as ei:
            await p.submit(_image_job())
        assert ei.value.category == "rate_limit"
        assert ei.value.retry_after == 5.0

    @respx.mock
    async def test_submit_no_data(self, agnes_settings, db):
        respx.post(f"{AGNES_BASE}{AGNES_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        with pytest.raises(ProviderError):
            await p.submit(_image_job())

    async def test_poll_unsupported(self, agnes_settings):
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        with pytest.raises(ProviderError):
            await p.poll(_image_job())

    @respx.mock
    async def test_healthcheck_not_configured(self, agnes_settings, monkeypatch):
        monkeypatch.delenv("AGNES_API_KEY", raising=False)
        monkeypatch.setattr(
            "app.providers.agnes_image.get_agnes_api_key", lambda: ""
        )
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        health = await p.healthcheck()
        assert health.state == "configured"

    @respx.mock
    async def test_healthcheck_ok(self, agnes_settings):
        respx.get(f"{AGNES_BASE}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        health = await p.healthcheck()
        assert health.state == "reachable"
        assert health.reachable is True

    @respx.mock
    async def test_healthcheck_401(self, agnes_settings):
        respx.get(f"{AGNES_BASE}/v1/models").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        health = await p.healthcheck()
        assert health.state == "authenticated"
        assert health.authenticated is False

    @respx.mock
    async def test_verify_success(self, agnes_settings):
        respx.post(f"{AGNES_BASE}{AGNES_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"data": [{"url": "https://x"}]})
        )
        p = _mk_provider(AgnesImageProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        health = await p.verify()
        assert health.state == "authenticated"
        assert p._auth_verified is True


class TestAgnesVideoProvider:
    @respx.mock
    async def test_submit_queued_remote(self, agnes_settings, db):
        respx.post(f"{AGNES_BASE}{CREATE_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={
                "video_id": "vid-1", "task_id": "task-1", "status": "queued",
            })
        )
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        res = await p.submit(_video_job())
        assert res.status == "queued_remote"
        assert res.remote_video_id == "vid-1"
        assert res.remote_task_id == "task-1"

    async def test_validate_i2v_requires_url(self, agnes_settings):
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        with pytest.raises(ProviderError):
            await p.validate_job(_video_job(type="image_to_video", requested_settings={}))

    async def test_validate_keyframes_requires_urls(self, agnes_settings):
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        with pytest.raises(ProviderError):
            await p.validate_job(_video_job(type="keyframes", requested_settings={}))

    async def test_validate_bad_frames(self, agnes_settings):
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        with pytest.raises(ProviderError):
            await p.validate_job(_video_job(requested_settings={"num_frames": 100}))

    @respx.mock
    async def test_poll_completed(self, agnes_settings, db):
        respx.get(f"{AGNES_BASE}{RESULT_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={
                "status": "completed",
                "metadata": {
                    "url": "https://cdn/vid.mp4",
                    "size_mapping": {"width": 1152, "height": 640},
                },
            })
        )
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        res = await p.poll({"id": 2, "remote_video_id": "vid-1"})
        assert res.status == "completed"
        assert res.remote_output_url == "https://cdn/vid.mp4"
        assert res.progress == 100

    @respx.mock
    async def test_poll_queued(self, agnes_settings, db):
        respx.get(f"{AGNES_BASE}{RESULT_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"status": "queued", "progress": 5})
        )
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        res = await p.poll({"id": 2, "remote_video_id": "vid-1"})
        assert res.status == "queued"

    @respx.mock
    async def test_poll_failed(self, agnes_settings, db):
        respx.get(f"{AGNES_BASE}{RESULT_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={
                "status": "failed", "error": {"message": "content policy"},
            })
        )
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        res = await p.poll({"id": 2, "remote_video_id": "vid-1"})
        assert res.status == "failed"
        assert "content policy" in res.error

    @respx.mock
    async def test_poll_in_progress(self, agnes_settings, db):
        respx.get(f"{AGNES_BASE}{RESULT_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"status": "in_progress", "progress": 42})
        )
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        res = await p.poll({"id": 2, "remote_video_id": "vid-1"})
        assert res.status == "processing"

    @respx.mock
    async def test_poll_completed_no_url(self, agnes_settings, db):
        respx.get(f"{AGNES_BASE}{RESULT_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"status": "completed", "metadata": {}})
        )
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, db)
        with pytest.raises(ProviderError):
            await p.poll({"id": 2, "remote_video_id": "vid-1"})

    async def test_poll_no_video_id(self, agnes_settings):
        p = _mk_provider(AgnesVideoProvider, httpx.AsyncClient(),
                         agnes_settings, None, None)
        with pytest.raises(ProviderError):
            await p.poll({"id": 2})


class TestAIHordeProvider:
    def test_always_configured(self):
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        assert p.is_configured() is True

    def test_anonymous_default(self, monkeypatch):
        monkeypatch.delenv("AI_HORDE_API_KEY", raising=False)
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        assert p.anonymous is True

    def test_capabilities(self, monkeypatch):
        monkeypatch.delenv("AI_HORDE_API_KEY", raising=False)
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        caps = p.capabilities()
        assert "text_to_image" in caps["job_types"]
        assert caps["anonymous"] is True

    @respx.mock
    async def test_active_models_cached(self, monkeypatch):
        respx.get(f"{HORDE_BASE}/v2/status/models").mock(
            return_value=httpx.Response(200, json=[{"name": "stable", "count": 3}])
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        models = await p.active_models(force=True)
        assert models[0]["name"] == "stable"

    @respx.mock
    async def test_active_models_network_error(self, monkeypatch):
        respx.get(f"{HORDE_BASE}/v2/status/models").mock(
            side_effect=httpx.ConnectError("boom")
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        with pytest.raises(ProviderError):
            await p.active_models(force=True)

    def test_pick_model_prefers_preferred(self):
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        models = [{"name": "a", "count": 1}, {"name": "b", "count": 0}]
        assert p._pick_model(models, "b") == "b"
        assert p._pick_model(models, None) == "a"

    async def test_validate_video_type_rejected(self):
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        with pytest.raises(ProviderError):
            await p.validate_job({"id": 1, "type": "text_to_video", "prompt": "x",
                                  "requested_settings": {}})

    async def test_validate_bad_dimensions(self):
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        with pytest.raises(ProviderError):
            await p.validate_job({"id": 1, "type": "text_to_image", "prompt": "x",
                                  "requested_settings": {"width": 10, "height": 10}})

    @respx.mock
    async def test_submit(self, monkeypatch, db):
        monkeypatch.setenv("AI_HORDE_API_KEY", "some-key")
        respx.get(f"{HORDE_BASE}/v2/status/models").mock(
            return_value=httpx.Response(200, json=[{"name": "stable", "count": 3}])
        )
        respx.post(f"{HORDE_BASE}/v2/generate/async").mock(
            return_value=httpx.Response(200, json={"id": "task-9", "message": "ok"})
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), _rl(), db)
        res = await p.submit({"id": 1, "type": "text_to_image", "prompt": "x",
                              "requested_settings": {"ratio": "1:1"}})
        assert res.status == "queued_remote"
        assert res.remote_task_id == "task-9"

    @respx.mock
    async def test_poll_not_done(self, db):
        respx.get(f"{HORDE_BASE}/v2/generate/check/task-9").mock(
            return_value=httpx.Response(200, json={"done": False, "waiting": 3})
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), _rl(), db)
        res = await p.poll({"id": 1, "remote_task_id": "task-9"})
        assert res.status == "processing"

    @respx.mock
    async def test_poll_faulted(self, db):
        respx.get(f"{HORDE_BASE}/v2/generate/check/task-9").mock(
            return_value=httpx.Response(200, json={"faulted": True})
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), _rl(), db)
        res = await p.poll({"id": 1, "remote_task_id": "task-9"})
        assert res.status == "failed"

    @respx.mock
    async def test_poll_completed(self, db):
        respx.get(f"{HORDE_BASE}/v2/generate/check/task-9").mock(
            return_value=httpx.Response(200, json={"done": True})
        )
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8).decode()
        respx.get(f"{HORDE_BASE}/v2/generate/status/task-9").mock(
            return_value=httpx.Response(200, json={
                "generations": [{"img": b64, "model": "stable", "seed": 3}],
            })
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), _rl(), db)
        res = await p.poll({"id": 1, "remote_task_id": "task-9"})
        assert res.status == "completed"
        assert res.b64_output == b64

    @respx.mock
    async def test_poll_censored(self, db):
        respx.get(f"{HORDE_BASE}/v2/generate/check/task-9").mock(
            return_value=httpx.Response(200, json={"done": True})
        )
        respx.get(f"{HORDE_BASE}/v2/generate/status/task-9").mock(
            return_value=httpx.Response(200, json={
                "generations": [{"img": None, "censored": True}],
            })
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), _rl(), db)
        res = await p.poll({"id": 1, "remote_task_id": "task-9"})
        assert res.status == "failed"
        assert "censored" in res.error

    @respx.mock
    async def test_poll_no_images(self, db):
        respx.get(f"{HORDE_BASE}/v2/generate/check/task-9").mock(
            return_value=httpx.Response(200, json={"done": True})
        )
        respx.get(f"{HORDE_BASE}/v2/generate/status/task-9").mock(
            return_value=httpx.Response(200, json={"generations": []})
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), _rl(), db)
        res = await p.poll({"id": 1, "remote_task_id": "task-9"})
        assert res.status == "failed"

    @respx.mock
    async def test_cancel(self, db):
        respx.delete(f"{HORDE_BASE}/v2/generate/status/task-9").mock(
            return_value=httpx.Response(200, json={"done": True})
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, db)
        await p.cancel({"id": 1, "remote_task_id": "task-9"})  # no raise

    @respx.mock
    async def test_healthcheck_ok(self, monkeypatch):
        respx.get(f"{HORDE_BASE}/v2/status/models").mock(
            return_value=httpx.Response(200, json=[{"name": "a"}])
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        health = await p.healthcheck()
        assert health.reachable is True

    @respx.mock
    async def test_healthcheck_429(self, monkeypatch):
        respx.get(f"{HORDE_BASE}/v2/status/models").mock(
            return_value=httpx.Response(429, text="slow")
        )
        p = AIHordeProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        health = await p.healthcheck()
        assert health.state == "rate_limited"


class TestProviderErrors:
    def test_classify_http_error(self):
        resp = httpx.Response(503, text="down")
        req = httpx.Request("GET", "https://x")
        exc = classify_http_error(httpx.HTTPStatusError("e", request=req, response=resp))
        assert exc.category == "server"
        assert exc.retryable is True

    def test_retryable_flags(self):
        assert ProviderError("rate_limit", "x").retryable is True
        assert ProviderError("auth", "x").retryable is False

    def test_backoff_seconds(self):
        assert backoff_seconds(1) > 0
        assert backoff_seconds(10, base=2.0, cap=5.0) <= 7.5


class TestDisabledProviders:
    def test_wangp_disabled(self):
        from app.providers.wangp_future import WangpProvider

        p = WangpProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        assert p.is_configured() is False

    def test_comfyui_disabled(self):
        from app.providers.comfyui_future import ComfyuiProvider

        p = ComfyuiProvider(httpx.AsyncClient(), _FakeSettings(), None, None)
        assert p.is_configured() is False


class TestCsrfViaDb:
    def test_token_roundtrip(self, db):
        c = CsrfProtector(db)
        t = c.token()
        c.validate(t)
