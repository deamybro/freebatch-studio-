"""Unit tests for security helpers."""

from __future__ import annotations

import pytest
from app.security import (
    CsrfProtector,
    SecurityError,
    mask_secret,
    safe_basename_for_job,
    safe_slug,
    sanitize_filename,
    validate_remote_url,
    zip_arcname_is_safe,
)


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------
class TestSanitizeFilename:
    def test_removes_path_separators(self):
        assert sanitize_filename("../../etc/passwd") == "passwd"

    def test_strips_illegal_chars(self):
        out = sanitize_filename('foo:"bar"?<baz>|*')
        assert "/" not in out and "\\" not in out and ":" not in out

    def test_windows_reserved_names(self):
        assert sanitize_filename("CON.txt").startswith("_")
        assert sanitize_filename("NUL").startswith("_")
        assert sanitize_filename("lpt1.log").startswith("_")

    def test_empty_becomes_output(self):
        assert sanitize_filename("") == "output"
        assert sanitize_filename("...") == "output"

    def test_keeps_extension_and_caps_length(self):
        out = sanitize_filename("a" * 300 + ".png", max_len=40)
        assert out.endswith(".png")
        assert len(out) <= 40

    def test_trailing_spaces_and_dots_stripped(self):
        assert sanitize_filename("name.png ") == "name.png"


# ---------------------------------------------------------------------------
# safe_slug / safe_basename_for_job
# ---------------------------------------------------------------------------
class TestSafeSlug:
    def test_ascii_only(self):
        assert safe_slug("héllo wörld") == "hello_world"

    def test_empty_defaults(self):
        assert safe_slug("!!!") == "output"

    def test_caps_length(self):
        assert len(safe_slug("x" * 100)) <= 40


class TestSafeBasenameForJob:
    def test_padded_index_and_slug(self):
        out = safe_basename_for_job(3, "Red circle", "png")
        assert out == "0003_Red_circle.png"

    def test_extension_normalized(self):
        out = safe_basename_for_job(0, "a", "PNG")
        assert out.endswith(".png")

    def test_unknown_extension_falls_back(self):
        # path separators are stripped so no traversal is possible
        out = safe_basename_for_job(0, "a", "../../evil")
        assert "/" not in out and "\\" not in out and ".." not in out


# ---------------------------------------------------------------------------
# mask_secret
# ---------------------------------------------------------------------------
class TestMaskSecret:
    def test_never_leaks_full_secret(self):
        masked = mask_secret("super-secret-api-key-value")
        assert "secret" not in masked
        assert "****" in masked

    def test_empty(self):
        assert mask_secret(None) == "Not configured"
        assert mask_secret("") == "Not configured"

    def test_short_secret_still_masked(self):
        masked = mask_secret("abcdef")
        assert masked == "ab****ef"


# ---------------------------------------------------------------------------
# validate_remote_url (SSRF guard)
# ---------------------------------------------------------------------------
class TestValidateRemoteUrl:
    def test_https_url_passes(self):
        assert validate_remote_url("https://example.com/a.png")

    def test_http_url_rejected_by_default(self):
        with pytest.raises(SecurityError):
            validate_remote_url("http://example.com/a.png")

    def test_http_url_passes_when_opted_in(self):
        assert validate_remote_url(
            "http://example.com/a.png", require_https=False
        )

    def test_rejects_non_http_schemes(self):
        with pytest.raises(SecurityError):
            validate_remote_url("file:///etc/passwd")

    def test_rejects_private_host(self):
        with pytest.raises(SecurityError):
            validate_remote_url("https://192.168.1.1/x.png")
        with pytest.raises(SecurityError):
            validate_remote_url("https://127.0.0.1/x.png")
        with pytest.raises(SecurityError):
            validate_remote_url("https://localhost/x.png")
        with pytest.raises(SecurityError):
            validate_remote_url("https://10.0.0.5/x.png")
        with pytest.raises(SecurityError):
            validate_remote_url("https://169.254.0.1/x.png")

    def test_allow_private_skips_guard(self):
        assert validate_remote_url("https://127.0.0.1/x.png", allow_private=True)

    def test_missing_host(self):
        with pytest.raises(SecurityError):
            validate_remote_url("https://")


# ---------------------------------------------------------------------------
# zip_arcname_is_safe
# ---------------------------------------------------------------------------
class TestZipArcnameIsSafe:
    def test_normal(self):
        assert zip_arcname_is_safe("batch_1/0001.png")

    def test_traversal(self):
        assert not zip_arcname_is_safe("../evil")
        assert not zip_arcname_is_safe("a/../../evil")
        assert not zip_arcname_is_safe("a/./b")

    def test_absolute_and_windows(self):
        assert not zip_arcname_is_safe("/abs/path")
        assert not zip_arcname_is_safe("C:/evil")

    def test_empty(self):
        assert not zip_arcname_is_safe("")


# ---------------------------------------------------------------------------
# CsrfProtector
# ---------------------------------------------------------------------------
class TestCsrfProtector:
    def test_token_generated_and_persisted(self, db):
        a = CsrfProtector(db)
        t1 = a.token()
        assert t1 and len(t1) >= 20
        b = CsrfProtector(db)
        assert b.token() == t1  # persisted, stable across instances

    def test_validate_ok(self, db):
        c = CsrfProtector(db)
        c.validate(c.token())

    def test_validate_fails(self, db):
        c = CsrfProtector(db)
        with pytest.raises(SecurityError):
            c.validate("wrong-token")
        with pytest.raises(SecurityError):
            c.validate(None)

    def test_check_env_setup(self, db):
        c = CsrfProtector(db)
        c.check_env_setup()
        assert c.token()
