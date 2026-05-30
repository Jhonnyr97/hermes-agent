"""Tests for web gateway custom features in the API server.

Covers:
- SessionDB conversation history loading (canonical pattern)
- Session context vars for Rails-originated cron delivery
- HMAC-signed file download endpoint
- Reasoning callback wiring through SSE
- Run status tracking lifecycle
- Multi-message input → conversation_history extraction
- reasoning.available fallback guard
"""

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    _SHARED_UPLOADS_DIR,
    cors_middleware,
    security_headers_middleware,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(api_key: str = "") -> APIServerAdapter:
    """Create an adapter with optional API key."""
    extra = {}
    if api_key:
        extra["key"] = api_key
    config = PlatformConfig(enabled=True, extra=extra)
    adapter = APIServerAdapter(config)
    return adapter


def _create_runs_app(adapter: APIServerAdapter) -> web.Application:
    """Create an aiohttp app with all /v1/runs routes registered."""
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/runs", adapter._handle_runs)
    app.router.add_get("/v1/runs/{run_id}", adapter._handle_get_run)
    app.router.add_get("/v1/runs/{run_id}/events", adapter._handle_run_events)
    app.router.add_post("/v1/runs/{run_id}/stop", adapter._handle_stop_run)
    app.router.add_get("/v1/files/{filename}/download", adapter._handle_file_download)
    return app


def _make_db_history(messages: list[dict]) -> list[dict]:
    """Build a fake SessionDB history list matching get_messages_as_conversation() output."""
    return messages


def _create_mock_session_db(*, history: list[dict] = None) -> MagicMock:
    """Create a mock SessionDB that returns canned history."""
    db = MagicMock()
    db.get_messages_as_conversation.return_value = history or []
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.fixture
def auth_adapter():
    return _make_adapter(api_key="sk-secret")


@pytest.fixture
def mock_agent():
    """Create a standard mock agent that returns quickly."""
    agent = MagicMock()
    agent.run_conversation.return_value = {"final_response": "done"}
    agent.session_prompt_tokens = 10
    agent.session_completion_tokens = 5
    agent.session_total_tokens = 15
    return agent


# ===========================================================================
# SessionDB conversation history loading (canonical pattern)
# ===========================================================================


class TestSessionDBHistoryLoading:
    """When no conversation_history is provided BUT session_id IS present,
    the API server must load history from SessionDB (mirroring gateway/run.py)."""

    @pytest.mark.asyncio
    async def test_loads_history_when_not_provided(self, adapter, mock_agent):
        """Given session_id + no explicit history → load from SessionDB."""
        db_history = [
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "The capital is Paris."},
        ]
        mock_db = _create_mock_session_db(history=db_history)

        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with (
                patch.object(adapter, "_create_agent", return_value=mock_agent),
                patch.object(adapter, "_ensure_session_db", return_value=mock_db),
            ):
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "Tell me more",
                        "session_id": "test-session-99",
                    },
                )
                assert resp.status == 202

                # Verify the agent was called with the loaded history
                assert mock_agent.run_conversation.called
                _call_kwargs = mock_agent.run_conversation.call_args.kwargs
                history_arg = _call_kwargs.get("conversation_history", [])
                assert len(history_arg) == 2
                assert history_arg[0]["role"] == "user"
                assert "France" in str(history_arg[0]["content"])

    @pytest.mark.asyncio
    async def test_does_not_load_when_explicit_history_given(self, adapter, mock_agent):
        """If the client sends explicit conversation_history, do NOT load from DB."""
        mock_db = _create_mock_session_db(history=[{"role": "user", "content": "SHOULD_NOT_BE_USED"}])
        explicit_history = [{"role": "user", "content": "Explicit question"}]

        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with (
                patch.object(adapter, "_create_agent", return_value=mock_agent),
                patch.object(adapter, "_ensure_session_db", return_value=mock_db),
            ):
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "Answer this",
                        "session_id": "test-session-99",
                        "conversation_history": explicit_history,
                    },
                )
                assert resp.status == 202

                _call_kwargs = mock_agent.run_conversation.call_args.kwargs
                history_arg = _call_kwargs.get("conversation_history", [])
                assert len(history_arg) == 1
                assert history_arg[0]["content"] == "Explicit question"

    @pytest.mark.asyncio
    async def test_skips_load_when_no_session_id(self, adapter, mock_agent):
        """Without session_id, skip DB loading entirely."""
        mock_db = _create_mock_session_db(history=[{"role": "user", "content": "SHOULD_NOT_BE_USED"}])

        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with (
                patch.object(adapter, "_create_agent", return_value=mock_agent),
                patch.object(adapter, "_ensure_session_db", return_value=mock_db),
            ):
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "No session"},
                )
                assert resp.status == 202

                _call_kwargs = mock_agent.run_conversation.call_args.kwargs
                history_arg = _call_kwargs.get("conversation_history", [])
                assert len(history_arg) == 0

    @pytest.mark.asyncio
    async def test_handles_empty_db_gracefully(self, adapter, mock_agent):
        """Empty DB result should not crash — history stays empty."""
        mock_db = _create_mock_session_db(history=[])

        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with (
                patch.object(adapter, "_create_agent", return_value=mock_agent),
                patch.object(adapter, "_ensure_session_db", return_value=mock_db),
            ):
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "session_id": "empty-session",
                    },
                )
                assert resp.status == 202

                _call_kwargs = mock_agent.run_conversation.call_args.kwargs
                history_arg = _call_kwargs.get("conversation_history", [])
                assert len(history_arg) == 0

    @pytest.mark.asyncio
    async def test_handles_db_exception_gracefully(self, adapter, mock_agent):
        """DB exception should not propagate — history stays empty."""
        mock_db = _create_mock_session_db()
        mock_db.get_messages_as_conversation.side_effect = RuntimeError("DB failure")

        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with (
                patch.object(adapter, "_create_agent", return_value=mock_agent),
                patch.object(adapter, "_ensure_session_db", return_value=mock_db),
            ):
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "session_id": "broken-session",
                    },
                )
                assert resp.status == 202

                _call_kwargs = mock_agent.run_conversation.call_args.kwargs
                history_arg = _call_kwargs.get("conversation_history", [])
                assert len(history_arg) == 0


# ===========================================================================
# Session context vars for Rails-originated cron delivery
# ===========================================================================


class TestSessionContextVars:
    """When session_id starts with 'rails-session-', set_session_vars must
    be called so tools (e.g. cronjob tool) can route deliveries back."""

    @pytest.mark.asyncio
    async def test_sets_context_for_rails_session(self, adapter, mock_agent):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with (
                patch.object(adapter, "_create_agent", return_value=mock_agent),
                patch("gateway.session_context.set_session_vars") as mock_set_vars,
            ):
                mock_set_vars.return_value = "token_xyz"

                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "session_id": "rails-session-42",
                    },
                )
                assert resp.status == 202

                # Verify set_session_vars was called with correct args
                mock_set_vars.assert_called_once()
                _call_kwargs = mock_set_vars.call_args.kwargs
                assert _call_kwargs["platform"] == "aziendaos"
                assert "42" in str(_call_kwargs["chat_id"])
                assert "aziendaos:42" in str(_call_kwargs["session_key"])

    @pytest.mark.asyncio
    async def test_does_not_set_context_for_non_rails_session(self, adapter, mock_agent):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with (
                patch.object(adapter, "_create_agent", return_value=mock_agent),
                patch("gateway.session_context.set_session_vars") as mock_set_vars,
            ):
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "session_id": "telegram:12345",
                    },
                )
                assert resp.status == 202
                mock_set_vars.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_malformed_rails_session_id(self, adapter, mock_agent):
        """rails-session- with empty ID should not crash."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with (
                patch.object(adapter, "_create_agent", return_value=mock_agent),
                patch("gateway.session_context.set_session_vars") as mock_set_vars,
            ):
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": "hello",
                        "session_id": "rails-session-",
                    },
                )
                assert resp.status == 202
                # When chat_id is blank, set_session_vars should NOT be called
                # (the code checks _chat_id before calling)
                mock_set_vars.assert_not_called()


# ===========================================================================
# HMAC-signed file download endpoint
# ===========================================================================


class TestHMACFileDownload:
    """GET /v1/files/{filename}/download endpoint with HMAC-signed URLs."""

    def _sign_url(self, filename: str, expires: int, api_key: str = "") -> str:
        """Compute the HMAC-SHA256 signature for a download URL."""
        raw = f"{filename}:{expires}"
        return hmac.new(api_key.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]

    @pytest.mark.asyncio
    async def test_returns_404_when_file_missing(self, adapter):
        """Valid signature + valid params + missing file → 404."""
        app = _create_runs_app(adapter)
        expires = int(time.time()) + 3600
        sig = self._sign_url("nonexistent.txt", expires)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get(
                f"/v1/files/nonexistent.txt/download?expires={expires}&sig={sig}"
            )
            assert resp.status == 404
            body = await resp.json()
            assert "not found" in body.get("error", "")

    @pytest.mark.asyncio
    async def test_returns_401_for_missing_params(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/files/test.txt/download")
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_returns_401_for_expired_link(self, adapter):
        app = _create_runs_app(adapter)
        expires = int(time.time()) - 60  # 1 minute ago
        sig = self._sign_url("test.txt", expires)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get(
                f"/v1/files/test.txt/download?expires={expires}&sig={sig}"
            )
            assert resp.status == 401
            body = await resp.json()
            assert "expired" in body.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_returns_403_for_invalid_signature(self, adapter):
        app = _create_runs_app(adapter)
        expires = int(time.time()) + 3600

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get(
                f"/v1/files/test.txt/download?expires={expires}&sig=invalid"
            )
            assert resp.status == 403

    @pytest.mark.asyncio
    async def test_returns_400_for_invalid_expires(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get(
                "/v1/files/test.txt/download?expires=notanumber&sig=abc123"
            )
            assert resp.status == 400  # int() raises ValueError → 400

    @pytest.mark.asyncio
    async def test_returns_400_for_path_traversal_filename(self, adapter):
        """Path traversal in filename is detected by aiohttp (404) before reaching handler."""
        # aiohttp normalizes '../' in URLs before routing, so a traversal
        # attempt becomes a 404 (no matching route) instead of reaching our
        # handler. This is correct security behavior at the HTTP layer.
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get(
                "/v1/files/../secrets.txt/download"
            )
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_returns_400_for_slash_in_filename(self, adapter):
        """Handler rejects filenames containing '/' (path traversal via %2F)."""
        # aiohttp does NOT normalize %2F in route params, so 'dir%2Ffile.txt'
        # reaches the handler as 'dir/file.txt'. The handler checks for '/'
        # and returns 400.
        app = _create_runs_app(adapter)
        expires = int(time.time()) + 3600
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get(
                f"/v1/files/dir%2Ffile.txt/download?expires={expires}&sig=test"
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_uses_api_key_for_signing(self, tmp_path):
        """Verify that the API key is used as HMAC secret (adapter with key)."""
        adapter = _make_adapter(api_key="my-secret-key")
        app = _create_runs_app(adapter)
        expires = int(time.time()) + 3600

        # Create a temp file in the shared uploads dir
        test_file = tmp_path / "hmac_test_file.txt"
        test_file.write_text("test content")

        # Sign with the adapter's key
        sig = self._sign_url("hmac_test_file.txt", expires, api_key="my-secret-key")

        from unittest.mock import patch as _patch
        with _patch("gateway.platforms.api_server._SHARED_UPLOADS_DIR", tmp_path):
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.get(
                    f"/v1/files/hmac_test_file.txt/download?expires={expires}&sig={sig}"
                )
                assert resp.status == 200
                body = await resp.read()
                assert body == b"test content"


# ===========================================================================
# Reasoning callback wiring through SSE
# ===========================================================================


class TestReasoningCallback:
    """Verify that reasoning_callback is wired to _create_agent and
    produces correct reasoning.available SSE events."""

    @pytest.mark.asyncio
    async def test_reasoning_callback_passed_to_agent(self, adapter, mock_agent):
        """The reasoning_callback kwarg must be passed to _create_agent."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent) as mock_create:
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "think step by step"},
                )
                assert resp.status == 202

                # Verify _create_agent was called with a reasoning_callback
                assert mock_create.called
                _call_kwargs = mock_create.call_args.kwargs
                assert "reasoning_callback" in _call_kwargs
                assert callable(_call_kwargs["reasoning_callback"])

    @pytest.mark.asyncio
    async def test_reasoning_callback_invocation(self, adapter):
        """Calling the reasoning callback must push to the SSE queue."""
        app = _create_runs_app(adapter)

        def _make_agent_with_reasoning(**kwargs):
            agent = MagicMock()
            agent.run_conversation.return_value = {"final_response": "done"}
            agent.session_prompt_tokens = 10
            agent.session_completion_tokens = 5
            agent.session_total_tokens = 15

            # Simulate callback invocation inside run_conversation
            def _run_side(user_message=None, conversation_history=None, task_id=None, **kw):
                rc = kwargs.get("reasoning_callback")
                if rc:
                    rc("Step 1: analyze...")
                    rc("Step 2: compute...")
                return {"final_response": "done"}
            agent.run_conversation.side_effect = _run_side
            return agent

        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", side_effect=_make_agent_with_reasoning):
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "think about this"},
                )
                assert resp.status == 202
                data = await resp.json()
                run_id = data["run_id"]

                # Wait for the async task to finish
                await asyncio.sleep(0.3)

                # After run, the fired flag should be True
                assert adapter._run_reasoning_callback_fired.get(run_id) is True

                # SSE queue should have reasoning.available events
                queue = adapter._run_streams.get(run_id)
                if queue:
                    reasoning_events = []
                    while not queue.empty():
                        try:
                            ev = queue.get_nowait()
                            if ev and ev.get("event") == "reasoning.available":
                                reasoning_events.append(ev)
                        except asyncio.QueueEmpty:
                            break
                    assert len(reasoning_events) > 0
                    assert "Step 1" in reasoning_events[0].get("text", "")


# ===========================================================================
# Run status tracking lifecycle
# ===========================================================================


class TestRunStatusTracking:
    """Verify that runs transition through queued → running → completed/failed."""

    @pytest.mark.asyncio
    async def test_status_starts_queued(self, adapter, mock_agent):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "hello"},
                )
                assert resp.status == 202
                data = await resp.json()

                # Immediately after start, status should be queued or running
                status_resp = await cli.get(f"/v1/runs/{data['run_id']}")
                assert status_resp.status == 200
                status = await status_resp.json()
                assert status["status"] in ("queued", "running", "completed")

    @pytest.mark.asyncio
    async def test_get_run_returns_object_structure(self, adapter, mock_agent):
        """Pollable status response must have hermes.run object format."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                resp = await cli.post(
                    "/v1/runs",
                    json={"input": "hello"},
                )
                data = await resp.json()
                run_id = data["run_id"]

                status_resp = await cli.get(f"/v1/runs/{run_id}")
                status = await status_resp.json()

                assert status["run_id"] == run_id
                assert status["object"] == "hermes.run"
                assert "status" in status
                assert "model" in status
                assert "created_at" in status

    @pytest.mark.asyncio
    async def test_get_unknown_run_returns_404(self, adapter):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/runs/run_nonexistent")
            assert resp.status == 404


# ===========================================================================
# Multi-message input → conversation_history extraction
# ===========================================================================


class TestMultiMessageInput:
    """When input is an array of messages, the last becomes user_message
    and the rest become conversation_history."""

    @pytest.mark.asyncio
    async def test_extracts_history_from_multi_message_array(self, adapter, mock_agent):
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": [
                            {"role": "user", "content": "First question"},
                            {"role": "assistant", "content": "First answer"},
                            {"role": "user", "content": "Second question"},
                        ]
                    },
                )
                assert resp.status == 202

                _call_kwargs = mock_agent.run_conversation.call_args.kwargs
                history = _call_kwargs.get("conversation_history", [])

                # The first two messages should be extracted as history
                assert len(history) == 2
                assert history[0]["role"] == "user"
                assert history[0]["content"] == "First question"
                assert history[1]["role"] == "assistant"
                assert history[1]["content"] == "First answer"

                # The last message should be the user_message
                assert _call_kwargs["user_message"] == "Second question"

    @pytest.mark.asyncio
    async def test_multi_array_skipped_when_explicit_history(self, adapter, mock_agent):
        """Explicit conversation_history takes priority over array extraction."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                resp = await cli.post(
                    "/v1/runs",
                    json={
                        "input": [
                            {"role": "user", "content": "Ignore this"},
                            {"role": "assistant", "content": "Ignore this too"},
                            {"role": "user", "content": "Last"},
                        ],
                        "conversation_history": [
                            {"role": "user", "content": "Explicit"}
                        ],
                    },
                )
                assert resp.status == 202

                _call_kwargs = mock_agent.run_conversation.call_args.kwargs
                history = _call_kwargs.get("conversation_history", [])
                assert len(history) == 1
                assert history[0]["content"] == "Explicit"


# ===========================================================================
# Error handling — graceful degradation
# ===========================================================================


class TestGracefulDegradation:
    """Verify that our custom features don't throw unhandled exceptions."""

    @pytest.mark.asyncio
    async def test_bad_session_id_does_not_crash(self, adapter, mock_agent):
        """Edge case: unusual session_id values must not crash."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                for bad_id in ["", None, "   ", "no-prefix"]:
                    body = {"input": "hello"}
                    if bad_id is not None:
                        body["session_id"] = bad_id

                    resp = await cli.post("/v1/runs", json=body)
                    assert resp.status == 202

    @pytest.mark.asyncio
    async def test_concurrent_runs_dont_interfere(self, adapter, mock_agent):
        """Two runs with different session_ids should have independent state."""
        app = _create_runs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_create_agent", return_value=mock_agent):
                resp1 = await cli.post(
                    "/v1/runs", json={"input": "run A", "session_id": "session-a"}
                )
                resp2 = await cli.post(
                    "/v1/runs", json={"input": "run B", "session_id": "session-b"}
                )
                assert resp1.status == 202
                assert resp2.status == 202

                data1 = await resp1.json()
                data2 = await resp2.json()

                # Different run IDs
                assert data1["run_id"] != data2["run_id"]

                # Each should be independently trackable
                status1 = await cli.get(f"/v1/runs/{data1['run_id']}")
                status2 = await cli.get(f"/v1/runs/{data2['run_id']}")
                assert (await status1.json())["status"] in ("queued", "running", "completed")
                assert (await status2.json())["status"] in ("queued", "running", "completed")
