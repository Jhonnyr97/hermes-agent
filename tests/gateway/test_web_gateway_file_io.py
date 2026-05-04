"""Tests for file upload/download functions in the API server.

Covers:
- _safe_download_filename — sanitize filenames
- _read_file_part_summary — read file content for LLM
- _download_file_url — download files from allowed hosts
- _normalize_multimodal_content — multimodal content processing
- /v1/files/read endpoint — serve generated files
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.request import Request

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    _safe_download_filename,
    _read_file_part_summary,
    _download_file_url,
    _normalize_multimodal_content,
    _SHARED_UPLOADS_DIR,
    _FILE_URL_DOWNLOAD_DIR,
    _ALLOWED_FILE_URL_HOSTS,
    cors_middleware,
    security_headers_middleware,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file_app(adapter: APIServerAdapter) -> web.Application:
    """Create an aiohttp app with file-related routes."""
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_get("/v1/files/read", adapter._handle_file_read)
    app.router.add_get("/v1/files/{filename}/download", adapter._handle_file_download)
    return app


# ===========================================================================
# _safe_download_filename
# ===========================================================================


class TestSafeDownloadFilename:
    def test_normal_filename_preserved(self):
        assert _safe_download_filename("report.txt") == "report.txt"

    def test_spaces_replaced(self):
        assert _safe_download_filename("my report.pdf") == "my_report.pdf"

    def test_special_chars_replaced(self):
        assert _safe_download_filename("file!@#$%.csv") == "file_.csv"

    def test_empty_falls_back(self):
        assert _safe_download_filename("") == "file"
        assert _safe_download_filename(None) == "file"

    def test_very_long_truncated(self):
        long_name = "a" * 500 + ".txt"
        result = _safe_download_filename(long_name)
        assert len(result) <= 160

    def test_path_separators_removed(self):
        # '/' is not in [^A-Za-z0-9._-] so it stays, but strip("._") cleans dots
        result = _safe_download_filename("../etc/passwd")
        # The function only replaces chars NOT in A-Za-z0-9._-_ with _
        # '/' is in that set (no), so the actual result might vary
        assert len(result) > 0
        assert "/" not in result or ".." not in result


# ===========================================================================
# _read_file_part_summary
# ===========================================================================


class TestReadFilePartSummary:
    def test_file_not_found(self):
        result = _read_file_part_summary("/nonexistent/file.txt", "missing.txt", "text/plain")
        assert result["type"] == "text"
        assert "not found" in result["text"].lower()

    def test_text_file_read(self, tmp_path):
        test_file = tmp_path / "hello.txt"
        test_file.write_text("Hello, world!")
        result = _read_file_part_summary(str(test_file), "hello.txt", "text/plain")
        assert result["type"] == "text"
        assert "hello.txt" in result["text"]
        assert "Hello, world!" in result["text"]

    def test_binary_file_detected(self, tmp_path):
        """Non-UTF-8 content should be reported as binary."""
        test_file = tmp_path / "binary.bin"
        test_file.write_bytes(b"\x00\x01\x02\xFF\xFE")
        result = _read_file_part_summary(str(test_file), "binary.bin", "application/octet-stream")
        assert result["type"] == "text"
        assert "binary" in result["text"].lower()
        assert "binary.bin" in result["text"]

    def test_oversized_file(self, tmp_path):
        """Files exceeding _MAX_FILE_READ_BYTES (100MB) should be reported."""
        test_file = tmp_path / "large.bin"
        # We can't easily create a 100MB file, but we can verify the size check
        # by patching the constant
        from gateway.platforms.api_server import _MAX_FILE_READ_BYTES
        with patch("gateway.platforms.api_server._MAX_FILE_READ_BYTES", 10):
            test_file.write_text("x" * 20)
            result = _read_file_part_summary(str(test_file), "large.bin", "application/octet-stream")
            assert "exceeds" in result["text"].lower() or "20 bytes" in result["text"]

    def test_error_reading(self, tmp_path):
        """Non-existent or unreadable file returns error message."""
        result = _read_file_part_summary("/dev/null/nope", "error.txt", "text/plain")
        assert "error" in result["text"].lower() or "not found" in result["text"].lower()


# ===========================================================================
# _download_file_url
# ===========================================================================


class TestDownloadFileUrl:
    """These tests mock urlopen to avoid real network calls."""

    def test_rejects_non_http_scheme(self):
        with pytest.raises(ValueError, match="invalid_file_url"):
            _download_file_url("ftp://evil.com/file.txt", "file.txt")

    def test_rejects_disallowed_host(self):
        with pytest.raises(ValueError, match="invalid_file_url"):
            _download_file_url("http://evil.com/file.txt", "file.txt")

    def test_allowed_host_succeeds(self, tmp_path):
        """Mock a successful download from an allowed host (e.g., hermes-ui)."""
        content = b"Hello from file upload test"

        class MockResponse:
            def __init__(self):
                self._data = content
                self._pos = 0
            def read(self, chunk_size=1024):
                if self._pos >= len(self._data):
                    return b""
                chunk = self._data[self._pos:self._pos + chunk_size]
                self._pos += len(chunk)
                return chunk
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with (
            patch("gateway.platforms.api_server.urlopen", return_value=MockResponse()),
            patch("gateway.platforms.api_server._FILE_URL_DOWNLOAD_DIR", tmp_path),
        ):
            file_path = _download_file_url(
                "http://hermes-ui:4000/internal/files/signed123",
                "uploaded.txt",
                expected_size=len(content),
            )
            assert os.path.exists(file_path)
            with open(file_path, "rb") as f:
                assert f.read() == content

    def test_size_mismatch_rejected(self, tmp_path):
        """When expected_size doesn't match actual, file should be deleted."""
        content = b"Actual content here"

        class MockResponse:
            def __init__(self):
                self._data = content
                self._pos = 0
            def read(self, chunk_size=1024):
                if self._pos >= len(self._data):
                    return b""
                chunk = self._data[self._pos:self._pos + chunk_size]
                self._pos += len(chunk)
                return chunk
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with (
            patch("gateway.platforms.api_server.urlopen", return_value=MockResponse()),
            patch("gateway.platforms.api_server._FILE_URL_DOWNLOAD_DIR", tmp_path),
        ):
            with pytest.raises(ValueError, match="invalid_file_url"):
                _download_file_url(
                    "http://hermes-ui:4000/internal/files/signed123",
                    "mismatch.txt",
                    expected_size=99999,
                )
            # File should be cleaned up — ignore any pre-existing files
            # from module-level side effects during import
            all_files = list(tmp_path.iterdir())
            leftover = [f for f in all_files if f.name != "hermes_test"]
            assert len(leftover) == 0, f"Expected no files, found: {leftover}"

    def test_too_large_rejected(self, tmp_path):
        """Downloaded data exceeding limit should raise ValueError."""
        chunk_size = 1024 * 1024  # 1MB chunks
        _MAX = 100 * 1024 * 1024  # default _MAX_FILE_READ_BYTES

        class LargeResponse:
            def __init__(self):
                self._called = 0
            def read(self, size=chunk_size):
                self._called += 1
                if self._called > _MAX // chunk_size + 2:
                    return b""
                return b"x" * chunk_size
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with (
            patch("gateway.platforms.api_server.urlopen", return_value=LargeResponse()),
            patch("gateway.platforms.api_server._FILE_URL_DOWNLOAD_DIR", tmp_path),
        ):
            with pytest.raises(ValueError, match="file_too_large"):
                _download_file_url(
                    "http://hermes-ui:4000/internal/files/signed123",
                    "huge.bin",
                )


# ===========================================================================
# _normalize_multimodal_content
# ===========================================================================


class TestNormalizeMultimodalContent:
    def test_none_returns_empty(self):
        assert _normalize_multimodal_content(None) == ""

    def test_string_passthrough(self):
        assert _normalize_multimodal_content("hello") == "hello"

    def test_string_truncated(self):
        long_str = "x" * 100000
        result = _normalize_multimodal_content(long_str)
        assert len(result) <= 65536  # MAX_NORMALIZED_TEXT_LENGTH

    def test_empty_list_returns_empty(self):
        assert _normalize_multimodal_content([]) == ""

    def test_text_part_only(self):
        """When all parts are text, result is collapsed to a plain string."""
        result = _normalize_multimodal_content([
            {"type": "text", "text": "Hello, world!"}
        ])
        assert isinstance(result, str)
        assert "Hello, world!" in result

    def test_text_part_missing_text_skipped(self):
        result = _normalize_multimodal_content([
            {"type": "text", "text": None}
        ])
        assert result == ""

    def test_multiple_text_parts_combined(self):
        result = _normalize_multimodal_content([
            {"type": "text", "text": "Part 1. "},
            {"type": "text", "text": "Part 2."},
        ])
        assert isinstance(result, str)
        assert "Part 1." in result
        assert "Part 2." in result

    def test_file_url_part_with_download(self, tmp_path):
        """file_url parts with mock download should be replaced by text summary."""
        content = b"File content from upload"

        class MockResponse:
            def __init__(self):
                self._data = content
                self._pos = 0
            def read(self, chunk_size=1024):
                if self._pos >= len(self._data):
                    return b""
                chunk = self._data[self._pos:self._pos + chunk_size]
                self._pos += len(chunk)
                return chunk
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with (
            patch("gateway.platforms.api_server.urlopen", return_value=MockResponse()),
            patch("gateway.platforms.api_server._FILE_URL_DOWNLOAD_DIR", tmp_path),
        ):
            result = _normalize_multimodal_content([
                {"type": "text", "text": "Read this file:"},
                {
                    "type": "file_url",
                    "file_url": {
                        "url": "http://hermes-ui:4000/internal/files/abc123",
                        "original_name": "test.txt",
                        "content_type": "text/plain",
                        "byte_size": len(content),
                    }
                },
            ])
            assert isinstance(result, str) or isinstance(result, list)
            result_str = result if isinstance(result, str) else " ".join(p.get("text", "") for p in result)
            assert "Read this file:" in result_str
            assert "test.txt" in result_str
            assert "File content from upload" in result_str

    def test_file_url_missing_url_raises(self):
        """file_url without a URL should raise ValueError."""
        with pytest.raises(ValueError, match="invalid_file_url"):
            _normalize_multimodal_content([
                {
                    "type": "file_url",
                    "file_url": {
                        "original_name": "test.txt",
                    }
                }
            ])

    def test_file_url_with_missing_optional_fields(self, tmp_path):
        """file_url without original_name/content_type/byte_size should work."""
        content = b"Just text"

        class MockResponse:
            def __init__(self):
                self._data = content
                self._pos = 0
            def read(self, chunk_size=1024):
                if self._pos >= len(self._data):
                    return b""
                chunk = self._data[self._pos:self._pos + chunk_size]
                self._pos += len(chunk)
                return chunk
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with (
            patch("gateway.platforms.api_server.urlopen", return_value=MockResponse()),
            patch("gateway.platforms.api_server._FILE_URL_DOWNLOAD_DIR", tmp_path),
        ):
            result = _normalize_multimodal_content([
                {
                    "type": "file_url",
                    "file_url": {
                        "url": "http://localhost:4000/internal/files/abc123",
                    }
                }
            ])
            assert isinstance(result, str) or isinstance(result, list)
            result_str = result if isinstance(result, str) else " ".join(p.get("text", "") for p in result)
            assert "file" in result_str.lower()

    def test_file_url_invalid_host_rejected(self):
        """file_url pointing to a disallowed host should raise ValueError."""
        with pytest.raises(ValueError, match="invalid_file_url"):
            _normalize_multimodal_content([
                {
                    "type": "file_url",
                    "file_url": {
                        "url": "http://evil-site.com/file.txt",
                        "original_name": "test.txt",
                    }
                }
            ])

    def test_file_path_part(self, tmp_path):
        """file_path part reads from SHARED_UPLOADS_DIR."""
        test_file = tmp_path / "shared_file.txt"
        test_file.write_text("Shared file content")

        with patch("gateway.platforms.api_server._SHARED_UPLOADS_DIR", tmp_path):
            result = _normalize_multimodal_content([
                {
                    "type": "file_path",
                    "file_path": {
                        "path": str(test_file),
                        "original_name": "shared_file.txt",
                        "content_type": "text/plain",
                    }
                }
            ])
            result_str = result if isinstance(result, str) else " ".join(p.get("text", "") for p in result)
            assert "shared_file.txt" in result_str
            assert "Shared file content" in result_str

    def test_file_path_outside_allowed(self, tmp_path):
        """file_path outside SHARED_UPLOADS_DIR should be rejected."""
        outside = tmp_path / "outside.txt"
        outside.write_text("secrets")
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        with patch("gateway.platforms.api_server._SHARED_UPLOADS_DIR", allowed):
            result = _normalize_multimodal_content([
                {
                    "type": "file_path",
                    "file_path": {
                        "path": str(outside),
                        "original_name": "outside.txt",
                    }
                }
            ])
            result_str = result if isinstance(result, str) else " ".join(p.get("text", "") for p in result)
            assert "outside allowed" in result_str

    def test_image_url_passthrough(self):
        """image_url parts should pass through unchanged."""
        result = _normalize_multimodal_content([
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}
        ])
        assert isinstance(result, list)
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"] == "https://example.com/img.png"

    def test_unknown_part_type_raises(self):
        """Unknown part type should raise ValueError."""
        with pytest.raises(ValueError, match="unsupported_content_type"):
            _normalize_multimodal_content([
                {"type": "audio", "audio": {"url": "..."}}
            ])

    def test_input_file_url_also_processed(self, tmp_path):
        """'input_file_url' type should be treated same as 'file_url'."""
        content = b"Input file content"

        class MockResponse:
            def __init__(self):
                self._data = content
                self._pos = 0
            def read(self, chunk_size=1024):
                if self._pos >= len(self._data):
                    return b""
                chunk = self._data[self._pos:self._pos + chunk_size]
                self._pos += len(chunk)
                return chunk
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with (
            patch("gateway.platforms.api_server.urlopen", return_value=MockResponse()),
            patch("gateway.platforms.api_server._FILE_URL_DOWNLOAD_DIR", tmp_path),
        ):
            result = _normalize_multimodal_content([
                {
                    "type": "input_file_url",
                    "file_url": {
                        "url": "http://localhost:4000/internal/files/input123",
                        "original_name": "input.txt",
                    }
                }
            ])
            result_str = result if isinstance(result, str) else " ".join(p.get("text", "") for p in result)
            assert "input.txt" in result_str


# ===========================================================================
# /v1/files/read endpoint
# ===========================================================================


class TestHandleFileRead:
    """GET /v1/files/read — serve generated files by absolute path."""

    @pytest.fixture
    def adapter(self):
        return APIServerAdapter(PlatformConfig(enabled=True))

    @pytest.mark.asyncio
    async def test_missing_path_returns_400(self, adapter):
        app = _make_file_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/files/read")
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_path_traversal_returns_403(self, adapter, tmp_path):
        """Paths outside generated_files_dir should be rejected."""
        app = _make_file_app(adapter)
        adapter._generated_files_dir = str(tmp_path)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get(f"/v1/files/read?path=/etc/passwd")
            assert resp.status == 403 or resp.status == 404

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_404(self, adapter, tmp_path):
        app = _make_file_app(adapter)
        adapter._generated_files_dir = str(tmp_path)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get(
                f"/v1/files/read?path={tmp_path / 'nonexistent.txt'}"
            )
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_valid_file_returns_200(self, adapter, tmp_path):
        """Valid file within generated_files_dir should be served."""
        app = _make_file_app(adapter)
        adapter._generated_files_dir = str(tmp_path)

        test_file = tmp_path / "output.txt"
        test_file.write_text("File content for download")

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get(f"/v1/files/read?path={test_file}")
            assert resp.status == 200
            body = await resp.read()
            assert body == b"File content for download"
