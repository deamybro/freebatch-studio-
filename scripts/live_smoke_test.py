"""Guarded LIVE smoke test for FreeBatch Studio.

This script is OPT-IN and never runs during normal pytest runs.

Requirements:
  RUN_LIVE_FREE_TESTS=1          must be set
  AGNES_API_KEY=...              required for Agnes tests
  (optional) RUN_LIVE_VIDEO=1    also test one short Agnes video

What it does (minimal, free-cost safe):
  1. One Agnes Image 1K generation
  2. One small AI Horde image (anonymous or keyed)
  3. One short Agnes Video (3s) - only when RUN_LIVE_VIDEO=1

Safety:
  - refuses to run if FREE_ONLY_MODE is off
  - refuses unknown / non-free pricing metadata
  - never purchases credits, subscribes or adds billing info

Run:  .venv\\Scripts\\python.exe scripts\\live_smoke_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.app_state import AppState
from app.config import get_agnes_api_key, get_settings
from app.pricing import check_free_only, get_provider_meta

ENABLED = os.environ.get("RUN_LIVE_FREE_TESTS") == "1"
RUN_VIDEO = os.environ.get("RUN_LIVE_VIDEO") == "1"
AGNES_KEY = get_agnes_api_key().strip()


def banner(text: str) -> None:
    print("\n" + "=" * 64)
    print(text)
    print("=" * 64)


def check_safety(state: AppState, provider: str, model: str | None) -> None:
    free_only = state.runtime.get_bool("free_only_mode", True)
    if not free_only:
        raise SystemExit("REFUSED: FREE_ONLY_MODE is disabled. Live tests require FREE-ONLY.")
    print(f"  provider/model under test : {provider} / {model or 'dynamic'}")
    print(f"  FREE_ONLY_MODE            : {free_only}")
    meta = get_provider_meta(state.db, provider, model)
    status = meta.get("pricing_status") if meta else None
    print(f"  pricing metadata          : {status} (last verified {meta.get('last_verified') if meta else 'never'})")
    check_free_only(state.db, provider, model, True)


async def make_one_image_batch(state: AppState, provider: str, model: str, prompt: str,
                               settings: dict) -> int:
    batch_id = state.db.create_batch(f"live-{provider}-{int(time.time())}",
                                     "image", None, pricing_acknowledged=True)
    state.db.create_jobs(batch_id, [{
        "job_index": 0, "type": "text_to_image", "prompt": prompt,
        "negative_prompt": None, "provider": provider, "model": model,
        "fallback_provider": None, "max_attempts": 1,
        "requested_settings": settings,
    }])
    return batch_id


async def wait_for_batch(state: AppState, batch_id: int, timeout: float = 900.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = state.db.get_batch(batch_id)
        done = state.db.batch_counts(batch_id)
        if done[0] + done[1] == b["total_jobs"] and b["total_jobs"] > 0:
            return done[0] == b["total_jobs"]
        await asyncio.sleep(3)
    return False


async def main() -> None:
    if not ENABLED:
        print("Live tests disabled. Set RUN_LIVE_FREE_TESTS=1 (and AGNES_API_KEY) to opt in.")
        raise SystemExit(0)
    if not AGNES_KEY:
        print("AGNES_API_KEY is required for live tests.")
        raise SystemExit(1)

    state = AppState(get_settings())
    state.startup()
    try:
        # --- 1. Agnes Image 1K -----------------------------------------
        banner("TEST 1: Agnes Image 2.1 Flash - one 1K image (free)")
        check_safety(state, "agnes_image", "agnes-image-2.1-flash")
        b1 = await make_one_image_batch(
            state, "agnes_image", "agnes-image-2.1-flash",
            "A single red circle on a white background",
            {"size": "1K", "ratio": "1:1", "output_format": "url"},
        )
        ok1 = await wait_for_batch(state, b1, timeout=600)
        job1 = state.db.list_jobs(b1, limit=1)[0]
        print(f"  job status : {job1['status']}  attempts: {job1['attempts']}")
        print(f"  error      : {job1['error_message'] or 'none'}")
        print(f"  output     : {job1['local_output_path'] or 'MISSING'}")
        if not ok1:
            raise SystemExit("Agnes Image live test FAILED")

        # --- 2. AI Horde one small image --------------------------------
        banner("TEST 2: AI Horde - one small image (community/free)")
        check_safety(state, "ai_horde", None)
        b2 = await make_one_image_batch(
            state, "ai_horde", None,
            "A simple drawing of a single blue circle",
            {"width": 1024, "height": 1024, "steps": 12},
        )
        ok2 = await wait_for_batch(state, b2, timeout=1800)
        job2 = state.db.list_jobs(b2, limit=1)[0]
        print(f"  job status : {job2['status']}  attempts: {job2['attempts']}")
        print(f"  error      : {job2['error_message'] or 'none'}")
        print(f"  output     : {job2['local_output_path'] or 'MISSING'}")
        if not ok2:
            print("  NOTE: AI Horde is community-driven; a failure may be queue/worker capacity.")
            raise SystemExit("AI Horde live test FAILED")

        # --- 3. Agnes Video (optional) ----------------------------------
        if RUN_VIDEO:
            banner("TEST 3: Agnes Video V2.0 - one short 3s text-to-video")
            check_safety(state, "agnes_video", "agnes-video-v2.0")
            b3 = state.db.create_batch(f"live-agnes-video-{int(time.time())}",
                                       "video", None, pricing_acknowledged=True)
            state.db.create_jobs(b3, [{
                "job_index": 0, "type": "text_to_video",
                "prompt": "A cat stretching on a windowsill, soft light",
                "negative_prompt": None, "provider": "agnes_video",
                "model": "agnes-video-v2.0", "fallback_provider": None,
                "max_attempts": 1,
                "requested_settings": {
                    "duration": "3s", "num_frames": 81, "fps": 24,
                    "ratio": "16:9", "tier": "480p", "width": 832, "height": 448,
                },
            }])
            ok3 = await wait_for_batch(state, b3, timeout=1800)
            job3 = state.db.list_jobs(b3, limit=1)[0]
            print(f"  job status : {job3['status']}  attempts: {job3['attempts']}")
            print(f"  error      : {job3['error_message'] or 'none'}")
            print(f"  output     : {job3['local_output_path'] or 'MISSING'}")
            if not ok3:
                raise SystemExit("Agnes Video live test FAILED")
        else:
            print("\nSKIPPED Agnes video (set RUN_LIVE_VIDEO=1 to include it).")

        print("\nALL LIVE SMOKE TESTS PASSED (free-cost safe).")
    finally:
        await state.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
