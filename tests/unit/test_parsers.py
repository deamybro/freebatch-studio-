"""Unit tests for the TXT/CSV parsers."""

from __future__ import annotations

from app.parsers import job_slug, parse_csv, parse_input, parse_txt


class TestParseTxt:
    def test_one_prompt_per_line(self):
        assert parse_txt("alpha\n\nbeta\n  gamma  \n") == ["alpha", "beta", "gamma"]

    def test_empty(self):
        assert parse_txt("") == []
        assert parse_txt("\n\n") == []


class TestParseCsvImage:
    def test_minimal(self):
        jobs, errors = parse_csv(
            "prompt\nred circle\nblue square\n", "image", {}
        )
        assert len(jobs) == 2
        assert jobs[0]["type"] == "text_to_image"
        assert jobs[0]["provider"] == "agnes_image"
        assert jobs[0]["requested_settings"]["size"] == "1K"
        assert jobs[0]["requested_settings"]["ratio"] == "1:1"
        assert errors == []

    def test_missing_header(self):
        jobs, errors = parse_csv("hello\nworld\n", "image", {})
        assert jobs == []
        assert errors == ["CSV is missing a 'prompt' column"]

    def test_no_header_row(self):
        jobs, errors = parse_csv("", "image", {})
        assert jobs == []
        assert errors == ["CSV has no header row"]

    def test_missing_prompt_column(self):
        jobs, errors = parse_csv("a,b\nc,d\n", "image", {})
        assert jobs == []
        assert errors == ["CSV is missing a 'prompt' column"]

    def test_bad_size_and_ratio_reported(self):
        jobs, errors = parse_csv(
            "prompt,size,ratio\np,9K,9:9\n", "image", {}
        )
        assert jobs[0]["requested_settings"]["size"] == "1K"
        assert jobs[0]["requested_settings"]["ratio"] == "1:1"
        assert len(errors) == 2

    def test_i2i_requires_image(self):
        _, errors = parse_csv("prompt,type\np,image_to_image\n", "image", {})
        assert len(errors) == 1
        assert "requires at least one image_url" in errors[0]

    def test_i2i_with_image(self):
        jobs, errors = parse_csv(
            "prompt,type,image_url\np,image_to_image,https://x/a.png\n",
            "image", {},
        )
        assert jobs[0]["type"] == "image_to_image"
        assert jobs[0]["requested_settings"]["image_urls"] == ["https://x/a.png"]
        assert errors == []

    def test_multi_image_splits_on_pipe(self):
        jobs, _ = parse_csv(
            "prompt,type,image_url\np,multi_image,https://a.png|https://b.png\n",
            "image", {},
        )
        assert jobs[0]["requested_settings"]["image_urls"] == [
            "https://a.png", "https://b.png",
        ]

    def test_text_to_image_with_url_becomes_i2i(self):
        jobs, _ = parse_csv(
            "prompt,image_url\np,https://a.png\n", "image", {}
        )
        assert jobs[0]["type"] == "image_to_image"

    def test_duplicate_prompts_skipped(self):
        jobs, errors = parse_csv("prompt\ndup\ndup\n", "image", {})
        assert len(jobs) == 1
        assert len(errors) == 1
        assert "duplicate prompt skipped" in errors[0]

    def test_bom_stripped(self):
        jobs, errors = parse_csv("\ufeffprompt\nx\n", "image", {})
        assert len(jobs) == 1
        assert errors == []

    def test_ai_horde_gets_dimensions(self):
        jobs, _ = parse_csv(
            "prompt,provider,ratio\np,ai_horde,16:9\n", "image", {}
        )
        assert jobs[0]["provider"] == "ai_horde"
        assert jobs[0]["requested_settings"]["width"] == 1024
        assert jobs[0]["requested_settings"]["height"] == 576
        assert "size" not in jobs[0]["requested_settings"]


class TestParseCsvVideo:
    def test_defaults(self):
        jobs, errors = parse_csv("prompt\nclip\n", "video", {})
        assert jobs[0]["type"] == "text_to_video"
        assert jobs[0]["provider"] == "agnes_video"
        assert jobs[0]["requested_settings"]["num_frames"] == 121  # 5s
        assert jobs[0]["requested_settings"]["fps"] == 24
        assert errors == []

    def test_duration_preset(self):
        jobs, errors = parse_csv("prompt,duration\np,10s\n", "video", {})
        assert jobs[0]["requested_settings"]["num_frames"] == 241
        assert errors == []

    def test_bad_duration(self):
        jobs, errors = parse_csv("prompt,duration\np,99s\n", "video", {})
        assert jobs[0]["requested_settings"]["num_frames"] == 121
        assert len(errors) == 1

    def test_keyframes_requires_urls(self):
        _, errors = parse_csv(
            "prompt,type\np,keyframes\n", "video", {}
        )
        assert len(errors) == 1

    def test_keyframes_with_urls(self):
        jobs, errors = parse_csv(
            "prompt,type,keyframe_urls\np,keyframes,https://a.png|https://b.png\n",
            "video", {},
        )
        assert jobs[0]["requested_settings"]["keyframe_urls"] == [
            "https://a.png", "https://b.png",
        ]
        assert errors == []

    def test_i2v_requires_image(self):
        _, errors = parse_csv(
            "prompt,type\np,image_to_video\n", "video", {}
        )
        assert len(errors) == 1

    def test_t2v_with_image_becomes_i2v(self):
        jobs, errors = parse_csv(
            "prompt,image_url\np,https://x.png\n", "video", {}
        )
        assert jobs[0]["type"] == "image_to_video"
        assert jobs[0]["requested_settings"]["image_url"] == "https://x.png"
        assert errors == []

    def test_fps_clamped(self):
        jobs, _ = parse_csv("prompt,fps\np,999\n", "video", {})
        assert jobs[0]["requested_settings"]["fps"] == 60

    def test_seed_parsed(self):
        jobs, _ = parse_csv("prompt,seed\np,42\n", "image", {})
        assert jobs[0]["seed"] == 42


class TestParseInput:
    def test_csv_wins_over_text(self):
        jobs, errors = parse_input(
            "image", "line one\nline two", "prompt\ncsvline\n", {}
        )
        assert len(jobs) == 1
        assert jobs[0]["prompt"] == "csvline"
        assert errors == []

    def test_txt_fallback(self):
        jobs, errors = parse_input("video", "a\nb\n", None, {})
        assert len(jobs) == 2
        assert jobs[0]["type"] == "text_to_video"
        assert jobs[0]["provider"] == "agnes_video"
        assert errors == []

    def test_txt_with_defaults(self):
        jobs, _ = parse_input(
            "image", "a\n", None,
            {"provider": "ai_horde", "negative_prompt": "bad", "seed": 7},
        )
        assert jobs[0]["provider"] == "ai_horde"
        assert jobs[0]["negative_prompt"] == "bad"
        assert jobs[0]["seed"] == 7


class TestJobSlug:
    def test_slug(self):
        assert job_slug("a b") == "a_b"
        assert job_slug("") == "output"
