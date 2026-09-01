# Changelog

All notable changes to FreeBatch Studio are documented here.

## [1.0.0] - 2026-08-21

Initial release.

### Added

- Local web UI (vanilla HTML/CSS/JS, no framework): dashboard, new image batch,
  new video batch, batches, batch detail, providers, settings, logs.
- Batch text-to-image, image-to-image and multi-image via Agnes Image 2.1 Flash
  (1K-4K, 8 ratios, URL or base64 output).
- Batch short videos via Agnes Video V2.0: text-to-video, image-to-video,
  keyframes; 3s/5s/10s/18s durations, 480p/720p/1080p tiers, 8n+1 frame rule.
- AI Horde image fallback (async, anonymous mode supported).
- Resumable single-worker queue + async poller with atomic claims, restart
  recovery, and `.part`-file cleanup.
- Retries with exponential backoff + jitter and configured-provider fallback on
  technical errors only.
- FREE-ONLY pricing guard and stale-pricing warnings.
- Streaming downloads with magic-byte sniffing and size caps.
- Automatic `manifest.csv` / `manifest.json` per completed batch; ZIP export
  with traversal-safe members.
- Provider health checks (short 5s timeouts, 30s cache), "Verify now" one-shot
  generations, and live AI Horde model list.
- CSRF protection, SSRF-guarded remote URLs (HTTPS required by default), secret
  masking, and secret-redacting logs.
- TXT/CSV batch import with permissive parsing and preview errors.
- `setup.bat` / `start.bat` for Windows, `run.py` launcher, `.env.example`.

### Fixed

- Future-provider cards no longer duplicate the "intended for future local GPU
  hardware." text (was `DisabledProvider` + Wangp/ComfyUI appending it twice).
- Added the missing `agnes_video:poll` rate-limit key so video polling is
  capped at 1 RPM instead of silently falling back to the 10 RPM default.
- The **output directory** setting now actually takes effect: file placement,
  ZIP/manifest exports and the storage meter all resolve the live runtime value
  instead of the env default.
- AI Horde no longer claims `image_to_image` support (it silently ignored
  reference images); it is now text-to-image only and rejects other job types
  with a clear message.
- `_resolve_output` uses `Path.is_relative_to` instead of a fragile
  `startswith` prefix check.
- ZIP export honors `zip_include_failed`: when disabled, only completed-job
  outputs and manifests are archived (previous filter was dead code).
- `validate_remote_url` now requires HTTPS by default (`require_https=False`
  opts into plain http).
- Manifest writes cleaned up (`out_dir` no longer derived from batch name) and
  thread the runtime output root through the queue manager and API.
- Health checks use 5-second timeouts so the dashboard never blocks on a slow
  provider.

### Fixed

- `GET /api/providers` no longer returns HTTP 500. Provider `capabilities()` for
  Agnes Image exposed `AGNES_IMAGE_DIMENSIONS` keyed by `(ratio, tier)` tuples,
  which FastAPI could not serialize; capabilities are now passed through
  `security.json_safe()`, which stringifies non-string dict keys.
- **Config:** `AGNES_API_KEY`/`AI_HORDE_API_KEY` in `.env` were ignored — getters
  read only `os.environ`. Added fields to `app/config.py:Settings` and made
  getters read `get_settings()` (env overrides `.env`).
- **DB:** `app/database.py` returned `sqlite3.Row` (no `.get`); providers expect
  dicts. Now returns `dict(row)` — see `app/database.py:150`.
- **Queue:** `requested_settings` is a JSON string in the DB but providers expect
  dicts. Added `app/queue_manager.py:_parse_job_settings()` and applied in
  `_process_job`/`_poll_job`/`_handle_output`.
- **Providers:** `self.settings_provider.get(...)` fails on real `Settings`
  (pydantic has no `.get`). Added `app/providers/base.py:_setting()` and
  migrated 5 sites (`agnes_image.py:226`, `ai_horde.py:213,239`,
  `agnes_video.py:244,318`).

### Tests

- 275 unit + integration tests passing; `ruff` clean; coverage 85.7% (floor 80%).
- Added a regression test for tuple-keyed provider capabilities serialization.

## [Unreleased]

- Local GPU providers (WanGP / ComfyUI) once configured on future hardware.
- Per-job priority / reordering.