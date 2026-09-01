# FreeBatch Studio

A free, local batch-generation studio for images and short videos. Paste a list
of prompts (or import a CSV), pick free providers, and FreeBatch Studio queues,
retries, and downloads every output to your disk — with a local web UI and no
GPU, no Docker, and no cloud subscription required.

## Tagline

**FREE-ONLY batch image & video generation, fully on your own machine.**

## Disclaimer

FreeBatch Studio is **not affiliated with, endorsed by, or connected to** Agnes
AI or AI Horde. All provider names, models, and pricing information are used
only for interoperability and reflect the providers' own public documentation.

## Pricing warning

Free pricing can change at any time. The built-in FREE-ONLY mode refuses to run
any provider that is not verified as free, and the UI warns you when pricing
metadata is older than 14 days — especially before large batches. Always verify
current pricing with the providers before starting a very large batch.

## Features

- **Batch text-to-image** (Agnes Image 2.1 Flash) — sizes 1K/2K/3K/4K, ratios
  1:1 / 3:4 / 4:3 / 16:9 / 9:16 / 2:3 / 3:2 / 21:9, output as URL or base64.
- **Batch image-to-image and multi-image** (Agnes Image 2.1 Flash).
- **Batch short videos** (Agnes Video V2.0) — text-to-video, image-to-video,
  and keyframe-to-video; durations 3s/5s/10s/18s, 480p/720p/1080p tiers.
- **AI Horde as a free image fallback** (anonymous mode supported; text-to-image
  only).
- **Resumable local queue** — pause / resume / cancel / duplicate / retry-failed
  per batch, retry individual jobs, atomic claims so restarts never re-run a job.
- **Smart retries** with exponential backoff + jitter, and fallback to a
  configured fallback provider on technical failures (never on content-policy /
  auth / invalid-request errors).
- **FREE-ONLY guard** — paid or unverified providers are refused before any
  submission.
- **Streaming downloads** with `.part` files, magic-byte content sniffing,
  size caps, and safe rename.
- **Manifests** — `manifest.csv` and `manifest.json` are written automatically
  into every completed batch directory; a ZIP export (with optional manifests)
  is one click away.
- **Provider dashboard** — live health checks, "Verify now" generation tests,
  active AI Horde models, and FREE-ONLY pricing metadata.
- **Local web UI** — dashboard, new-batch forms with live preview, batch detail
  with per-job states, providers page, settings page, and rotating logs page.
- **CSRF protection, SSRF guard on remote URLs (HTTPS required by default),
  secret-masked API keys, and secret redaction in logs.**

## Requirements

- Windows 10/11, macOS, or Linux
- Python 3.11 or newer (tested on 3.13)
- ~300 MB disk for the app; plus space for outputs
- An internet connection (the app calls remote free providers)
- No GPU, no Docker, no Node.js required

## Install

Windows:

```
setup.bat
```

Manual:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Then copy `.env.example` to `.env` and add your API keys (see below).

## Configuration

Everything lives in `.env` (git-ignored). Copy from `.env.example` and edit:

| Variable | Purpose |
| --- | --- |
| `AGNES_API_KEY` | Agnes AI key (free). Creates one at the Agnes platform. |
| `AI_HORDE_API_KEY` | Optional. Leave empty for anonymous mode (lowest queue priority). |
| `HOST` / `PORT` | Bind address (default `127.0.0.1:8737`) and port. |
| `FREE_ONLY_MODE` | `true` refuses any provider not verified as free. |
| `OUTPUT_DIR` | Where completed outputs are stored (default `data/outputs`). |
| `POLL_INTERVAL_SECONDS` | Poll interval for async video / AI Horde jobs. |
| `MAX_ATTEMPTS` | Default retries per job. |
| `REQUEST_TIMEOUT_SECONDS` / `DOWNLOAD_TIMEOUT_SECONDS` | HTTP timeouts. |
| `MAX_DOWNLOAD_SIZE_MB` / `MAX_REFERENCE_SIZE_MB` | Download / reference size caps. |
| `PRICING_STALE_DAYS` / `LARGE_BATCH_WARN_THRESHOLD` | Stale-pricing warnings. |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |

Secrets are only ever read from the environment; they are never stored in
SQLite, returned by the API, or written to logs.

## Run

Windows:

```
start.bat
```

Manual:

```
.venv\Scripts\python run.py
```

Open http://127.0.0.1:8737 in your browser. `run.py --port 9000` changes the
port; `run.py --no-browser` skips auto-opening the browser. Press Ctrl+C to stop.

## Local UI / usage guide

1. **Dashboard** (`/`) — totals, live provider health, storage used, warnings.
2. **New Image Batch** (`/batches/new-image`) — paste prompts (one per line) or
   import CSV, pick provider / size / ratio, see a live parsed preview, then
   create the batch.
3. **New Video Batch** (`/batches/new-video`) — same flow for text-to-video,
   image-to-video (paste a public HTTPS image URL), and keyframes.
4. **Batches** (`/batches`) — all batches; open one to watch jobs, pause /
   resume / cancel / duplicate / retry-failed, download ZIP, and view the
   manifest. Per-job file downloads are available for completed jobs.
5. **Providers** (`/providers`) — health checks, "Verify now" tests, models,
   and pricing metadata.
6. **Settings** (`/settings`) — runtime settings (output dir, retries, poll
   interval, rate limits, FREE-ONLY mode, ZIP options, log level) and masked
   secret status.
7. **Logs** (`/logs`) — tail of `logs/app.log` with secrets redacted.

## TXT format

One non-empty line = one prompt. Example (`examples/prompts.txt`):

```
A red apple on a white table
A futuristic city at dusk
```

## CSV format

Flexible column order; `prompt` is required. See `examples/image_batch.csv` and
`examples/video_batch.csv`. Optional columns:

- Image: `type` (`text_to_image` / `image_to_image` / `multi_image`), `provider`,
  `model`, `size` (1K-4K), `ratio`, `negative_prompt`, `image_url`
  (pipe-separated for multi-image), `seed`.
- Video: `type` (`text_to_video` / `image_to_video` / `keyframes`), `provider`,
  `model`, `image_url`, `keyframe_urls` (pipe-separated), `negative_prompt`,
  `seed`, `duration` (3s/5s/10s/18s), `fps`, `width`, `height`.

Missing optional columns fall back to the form defaults. Invalid rows are
reported as preview errors, not silently dropped.

## Retry / fallback / error categories

- **Retryable** (technical): `rate_limit`, `timeout`, `network`, `server`,
  `quota`. Jobs are retried with backoff up to `max_attempts`, then may fall
  back to the configured fallback provider.
- **Never retried / never fall back** (safeguards): `auth`, `invalid_request`,
  `content_policy`, `provider_rejected`, `not_found`.
- Fallback only ever fires for technical availability problems — never for
  content policy or invalid input.

## FREE-ONLY mode

Enabled by default. Before every submission the pricing guard verifies the
provider/model is marked `free` and enabled. When disabled by an operator, the
guard still refuses providers with no pricing metadata rather than risk paid
generation.

## Directory layout

```
app/                 FastAPI app (routes, providers, queue, downloader, ...)
templates/ static/   UI (vanilla HTML/CSS/JS, no framework)
data/app.db          SQLite database (WAL mode)
data/outputs/<batch_id>/   completed files + manifest.csv + manifest.json
logs/app.log         rotating logs (secrets redacted)
examples/            sample TXT/CSV files
tests/               pytest suite
run.py start.bat setup.bat
```

## Security notes

- CSRF tokens are required on every mutating API call and compared in constant
  time.
- Remote image URLs are SSRF-guarded: private/loopback/link-local hosts are
  rejected and HTTPS is required by default.
- API keys are never logged, stored, or returned to the frontend; the UI shows
  only masked forms (`agn_****7F`).
- Output filenames are sanitized; ZIP members are validated against traversal;
  downloaded content is sniffed by magic bytes and size-capped.

## Tests

```
.venv\Scripts\python -m pytest            # 273 tests
.venv\Scripts\python -m pytest --cov=app  # coverage (>= 80% required)
.venv\Scripts\ruff check app tests scripts run.py
```

Live provider tests are opt-in (set `RUN_LIVE_FREE_TESTS=1` and export your API
keys) and are never part of a normal test run.

## Known limitations

- The app calls remote free providers; it performs **no local AI inference** and
  requires an internet connection.
- AI Horde is image-only and text-to-image only.
- image-to-video and keyframes require **publicly accessible HTTPS** image URLs;
  local files cannot be sent directly.
- Agnes Video does not document a cancel endpoint; cancelling stops local
  polling but remote computation may still finish.
- Live provider calls were not executed during development (no API keys); health
  checks and "Verify now" require valid keys.

## Troubleshooting

- **"AGNES_API_KEY is not configured"** — add the key to `.env` and restart.
- **Port already in use** — run with `--port 9000` or change `PORT` in `.env`.
- **Downloads fail with "unexpected content type"** — the provider returned a
  non-image/non-video body; check the job's error message in the batch detail.
- **Pricing warning banner** — free pricing metadata is older than
  `PRICING_STALE_DAYS`; verify pricing on the Providers page before large runs.
- **See `logs/app.log`** for redacted diagnostics of every submit/poll/fail.

## Roadmap

- Local GPU providers (WanGP / ComfyUI) once configured on future hardware —
  currently shown as disabled slots.
- More free providers as they become available.
- Per-job priority / reordering in the queue.

## License

Free for personal and small-scale use. Provider names and pricing belong to
their respective owners. See the LICENSE file (if present) for details.