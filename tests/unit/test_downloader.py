"""Unit tests for the streaming downloader."""

from __future__ import annotations

import pytest
import respx
from app.downloader import (
    DownloadError,
    cleanup_part_files,
    mime_is_allowed,
    parse_content_type,
    sniff_mime,
    stream_download,
    write_base64_image,
)

PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_HEAD = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 24
MP4_HEAD = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32


class TestSniffMime:
    def test_png(self):
        assert sniff_mime(PNG_HEAD) == "image/png"

    def test_jpeg(self):
        assert sniff_mime(JPEG_HEAD) in ("image/jpeg", "image/jpg")

    def test_webp(self):
        assert sniff_mime(WEBP_HEAD) == "image/webp"

    def test_mp4(self):
        assert sniff_mime(MP4_HEAD) in ("video/mp4", "video/quicktime")

    def test_unknown(self):
        assert sniff_mime(b"\x00\x01\x02") is None


class TestMimeIsAllowed:
    def test_none_mime_rejected(self):
        assert mime_is_allowed(None, None) is False

    def test_allowed_none_means_any(self):
        assert mime_is_allowed("image/png", None) is True

    def test_in_set(self):
        assert mime_is_allowed("image/png", {"image/png"}) is True
        assert mime_is_allowed("image/gif", {"image/png"}) is False


class TestParseContentType:
    def test_basic(self):
        assert parse_content_type("image/png") == "image/png"

    def test_with_params(self):
        assert parse_content_type("image/png; charset=utf-8") == "image/png"

    def test_none(self):
        assert parse_content_type(None) is None


class TestWriteBase64Image:
    def test_writes_valid_png(self, tmp_path):
        import base64

        b64 = base64.b64encode(PNG_HEAD)
        path = write_base64_image(b64.decode(), tmp_path, "img.png")
        assert path.name == "img.png"
        assert path.read_bytes() == PNG_HEAD

    def test_rejects_invalid_base64(self, tmp_path):
        with pytest.raises(DownloadError):
            write_base64_image("!!!not-base64!!!", tmp_path, "img.png")

    def test_rejects_empty(self, tmp_path):
        with pytest.raises(DownloadError):
            write_base64_image("", tmp_path, "img.png")

    def test_rejects_oversized(self, tmp_path):
        import base64

        b64 = base64.b64encode(PNG_HEAD * 1000)
        with pytest.raises(DownloadError):
            write_base64_image(
                b64.decode(), tmp_path, "img.png", max_size_mb=0
            )

    def test_rejects_wrong_mime(self, tmp_path):
        import base64

        b64 = base64.b64encode(b"not an image")
        with pytest.raises(DownloadError):
            write_base64_image(
                b64.decode(), tmp_path, "img.png",
                allowed_mimes={"image/png"},
            )


class TestStreamDownload:
    @respx.mock
    async def test_downloads_and_renames(self, tmp_path):
        url = "https://cdn.example.com/out.png"
        respx.get(url).mock(return_value=httpx_response(PNG_HEAD))
        path = await stream_download(
            _client(), url, tmp_path, "out.png", allowed_mimes={"image/png"},
        )
        assert path.name == "out.png"
        assert path.read_bytes() == PNG_HEAD
        assert not list(tmp_path.glob("*.part"))

    @respx.mock
    async def test_http_error(self, tmp_path):
        url = "https://cdn.example.com/missing.png"
        respx.get(url).mock(return_value=httpx_response(b"", status=404))
        with pytest.raises(DownloadError):
            await stream_download(_client(), url, tmp_path, "out.png")

    @respx.mock
    async def test_oversized_rejected(self, tmp_path):
        url = "https://cdn.example.com/big.png"
        respx.get(url).mock(return_value=httpx_response(PNG_HEAD * 2))
        with pytest.raises(DownloadError):
            await stream_download(
                _client(), url, tmp_path, "out.png", max_size_mb=0,
            )

    @respx.mock
    async def test_bad_mime_rejected(self, tmp_path):
        url = "https://cdn.example.com/evil.html"
        respx.get(url).mock(return_value=httpx_response(b"<html>hi</html>"))
        with pytest.raises(DownloadError):
            await stream_download(
                _client(), url, tmp_path, "out.png",
                allowed_mimes={"image/png"},
            )

    @respx.mock
    async def test_empty_body_rejected(self, tmp_path):
        url = "https://cdn.example.com/empty"
        respx.get(url).mock(return_value=httpx_response(b""))
        with pytest.raises(DownloadError):
            await stream_download(_client(), url, tmp_path, "out.png")

    @respx.mock
    async def test_ssrf_private_rejected(self, tmp_path):
        url = "https://127.0.0.1/out.png"
        with pytest.raises(DownloadError):
            await stream_download(_client(), url, tmp_path, "out.png")


class TestCleanupPartFiles:
    def test_removes_parts(self, tmp_path):
        (tmp_path / "a.part").write_text("x")
        (tmp_path / "keep.png").write_text("y")
        n = cleanup_part_files(tmp_path)
        assert n == 1
        assert not (tmp_path / "a.part").exists()
        assert (tmp_path / "keep.png").exists()

    def test_missing_dir(self, tmp_path):
        assert cleanup_part_files(tmp_path / "nope") == 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
import httpx  # noqa: E402


def httpx_response(content: bytes, status=200, headers=None):
    return httpx.Response(status_code=status, content=content, headers=headers)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)
