"""Tests for GET /v1/sessions/{session_id} and POST /v1/runs/{run_id}/approvals/{approval_id}.

Covers:
- Session metadata retrieval with resolution, includes, format params
- Approval decision flow (once/session/always/deny)
- Edge cases: auth, missing params, invalid decisions, DB errors, timeouts
- Job creation with origin for web delivery routing
- Trigger job with one-shot deliver/origin overrides
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    cors_middleware,
    security_headers_middleware,
)


# ---------------------------------------------------------------------------
# Fixtures (mirrored from test_web_gateway_features.py for isolation)
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.fixture
def auth_adapter():
    return _make_adapter(api_key="sk-secret")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(api_key: str = "") -> APIServerAdapter:
    extra = {}
    if api_key:
        extra["key"] = api_key
    config = PlatformConfig(enabled=True, extra=extra)
    adapter = APIServerAdapter(config)
    return adapter


def _create_session_app(adapter: APIServerAdapter) -> web.Application:
    """Create an aiohttp app with /v1/sessions routes."""
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_get("/v1/sessions/{session_id}", adapter._handle_get_session)
    app.router.add_get("/v1/sessions/{session_id}/runtime", adapter._handle_get_session_runtime)
    return app


def _create_approval_app(adapter: APIServerAdapter) -> web.Application:
    """Create an aiohttp app with /v1/runs routes for approval testing."""
    mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
    app = web.Application(middlewares=mws)
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/runs/{run_id}/approvals/{approval_id}", adapter._handle_run_approval)
    return app


def _mock_session_db(**overrides) -> MagicMock:
    """Create a mock SessionDB with configurable behavior."""
    db = MagicMock()

    # Default: session found
    session_data = overrides.get("session_data", {
        "message_count": 5,
        "tool_call_count": 2,
    })
    db.get_session.return_value = session_data

    # Default: no compression
    db.get_compression_tip.return_value = None
    db.resolve_resume_session_id.return_value = None

    # Default: export returns simple messages
    db.export_session.return_value = {
        "messages": [
            {"role": "user", "content": "Hello", "tool_calls": []},
            {"role": "assistant", "content": "Hi there!"},
        ],
    }

    # Default: conversation format
    db.get_messages_as_conversation.return_value = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]

    # Apply any overrides
    for key, value in overrides.items():
        if key == "db_session_none":
            db.get_session.return_value = None
        elif key == "get_session_side_effect":
            db.get_session.side_effect = value

    return db


# ===========================================================================
# GET /v1/sessions/{session_id}
# ===========================================================================


class TestGetSession:
    """Read-only session metadata and messages retrieval."""

    @pytest.mark.asyncio
    async def test_missing_session_id_returns_400(self, adapter):
        """Empty session_id path segment must return 400."""
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/sessions/")
            assert resp.status in (400, 404)

    @pytest.mark.asyncio
    async def test_invalid_session_id_returns_400(self, adapter):
        """Session_id with control characters must be rejected."""
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/sessions/abc%0Adef")
            assert resp.status == 400
            body = await resp.json()
            assert "Invalid" in body.get("error", {}).get("message", "")

    @pytest.mark.asyncio
    async def test_session_not_found_returns_404(self, adapter):
        """When SessionDB returns None for the given session_id, return 404."""
        db = _mock_session_db(db_session_none=True)
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/nonexistent")
                assert resp.status == 404
                body = await resp.json()
                assert "not found" in body.get("error", {}).get("message", "").lower()

    @pytest.mark.asyncio
    async def test_db_unavailable_returns_503(self, adapter):
        """When session storage is None/not configured, return 503."""
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=None):
                resp = await cli.get("/v1/sessions/my-session")
                assert resp.status == 503
                body = await resp.json()
                assert "not available" in body.get("error", {}).get("message", "").lower()

    @pytest.mark.asyncio
    async def test_valid_session_returns_metadata(self, adapter):
        """Basic session GET returns id, resolved_session_id, compressed, lineage, message_count."""
        db = _mock_session_db()
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/test-session-42")
                assert resp.status == 200
                body = await resp.json()
                assert body["id"] == "test-session-42"
                assert body["resolved_session_id"] == "test-session-42"
                assert body["compressed"] is False
                assert body["lineage"] == ["test-session-42"]
                assert body["message_count"] == 5
                assert body["tool_call_count"] == 2

    @pytest.mark.asyncio
    async def test_resolved_true_follows_compression_chain(self, adapter):
        """When resolved=true (default) and get_compression_tip returns a child, resolve to it."""
        db = _mock_session_db()
        db.get_compression_tip.return_value = "child-session"
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/parent-session")
                assert resp.status == 200
                body = await resp.json()
                assert body["resolved_session_id"] == "child-session"
                assert body["compressed"] is True
                assert body["lineage"] == ["parent-session", "child-session"]

    @pytest.mark.asyncio
    async def test_resolved_false_skips_compression(self, adapter):
        """When resolved=false, don't follow compression chain."""
        db = _mock_session_db()
        db.get_compression_tip.return_value = "child-session"
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/parent-session?resolved=false")
                assert resp.status == 200
                body = await resp.json()
                assert body["resolved_session_id"] == "parent-session"
                db.get_compression_tip.assert_not_called()

    @pytest.mark.asyncio
    async def test_include_messages_returns_messages(self, adapter):
        """Default include=messages returns messages array."""
        db = _mock_session_db()
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/test-session-42")
                body = await resp.json()
                assert "messages" in body
                assert len(body["messages"]) == 2

    @pytest.mark.asyncio
    async def test_exclude_messages_omits_messages(self, adapter):
        """When include does NOT contain 'messages', 'tool_calls', or 'reasoning',
        the messages field is absent."""
        db = _mock_session_db()
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/test-session-42?include=metadata")
                body = await resp.json()
                assert "messages" not in body

    @pytest.mark.asyncio
    async def test_exclude_tool_calls_strips_them(self, adapter):
        """When tool_calls is NOT in include set, tool_calls key is stripped from messages."""
        db = _mock_session_db()
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/test-session-42?include=messages")
                body = await resp.json()
                for msg in body["messages"]:
                    assert "tool_calls" not in msg

    @pytest.mark.asyncio
    async def test_include_tool_calls_preserves_them(self, adapter):
        """When tool_calls IS in include set, tool_calls key is preserved."""
        db = _mock_session_db()
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/test-session-42?include=messages,tool_calls")
                body = await resp.json()
                assert any("tool_calls" in msg for msg in body["messages"])

    @pytest.mark.asyncio
    async def test_format_conversation_uses_conversation_method(self, adapter):
        """format=conversation uses get_messages_as_conversation."""
        db = _mock_session_db()
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/test-session-42?format=conversation")
                assert resp.status == 200
                db.get_messages_as_conversation.assert_called_once()

    @pytest.mark.asyncio
    async def test_format_raw_uses_export_method(self, adapter):
        """format=raw (default) uses export_session."""
        db = _mock_session_db()
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/test-session-42?format=raw")
                assert resp.status == 200
                db.export_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_db_exception_returns_500(self, adapter):
        """When SessionDB.get_session raises, return 500."""
        db = _mock_session_db()
        db.get_session.side_effect = RuntimeError("DB crashed")
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/test-session-42")
                assert resp.status == 500

    @pytest.mark.asyncio
    async def test_auth_required_when_api_key_set(self, auth_adapter):
        """When API key is configured, reject requests without valid auth."""
        app = _create_session_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/sessions/test-session")
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_auth_passes_with_valid_key(self, auth_adapter):
        """When API key is valid, the request proceeds."""
        db = _mock_session_db()
        app = _create_session_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(auth_adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get(
                    "/v1/sessions/test-session",
                    headers={"Authorization": "Bearer sk-secret"},
                )
                assert resp.status == 200

    @pytest.mark.asyncio
    async def test_full_lineage_from_lineage_root(self, adapter):
        """When _session_lineage_root_to_tip is available, use it for full lineage."""
        db = _mock_session_db()
        db.get_compression_tip.return_value = "child-session"
        db._session_lineage_root_to_tip.return_value = ["root", "parent", "child"]
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_ensure_session_db", return_value=db):
                resp = await cli.get("/v1/sessions/parent-session")
                body = await resp.json()
                assert body["lineage"] == ["root", "parent", "child"]
                assert body["compressed"] is True


class TestGetSessionRuntime:
    @pytest.mark.asyncio
    async def test_runtime_metadata_returns_configured_and_effective_fields(self, adapter):
        db = _mock_session_db(session_data={
            "message_count": 5,
            "tool_call_count": 2,
            "model": "anthropic/claude-sonnet-4",
            "system_prompt": "assembled runtime prompt",
        })
        app = _create_session_app(adapter)
        cfg = {
            "model": {
                "default": "openrouter/auto",
                "provider": "openrouter",
                "base_url": "",
            },
            "agent": {
                "system_prompt": "global config prompt",
            },
            "display": {
                "personality": "helpful",
            },
        }

        async with TestClient(TestServer(app)) as cli:
            with (
                patch.object(adapter, "_ensure_session_db", return_value=db),
                patch("gateway.run._load_gateway_config", return_value=cfg),
                patch("gateway.run._resolve_gateway_model", return_value="openrouter/auto"),
            ):
                resp = await cli.get("/v1/sessions/test-session-42?include=runtime")
                assert resp.status == 200
                body = await resp.json()

        runtime = body["runtime"]
        assert runtime["object"] == "hermes.session.runtime"
        assert runtime["id"] == "test-session-42"
        assert runtime["runtime_controlled_by"] == "hermes"
        assert runtime["advertised_model"] == "hermes-agent"
        assert runtime["configured_model"] == "openrouter/auto"
        assert runtime["configured_provider"] == "openrouter"
        assert runtime["effective_model"] == "anthropic/claude-sonnet-4"
        assert runtime["display_personality"] == "helpful"
        assert runtime["configured_system_prompt"] == "global config prompt"
        assert runtime["assembled_system_prompt"] == "assembled runtime prompt"
        assert "config.agent.system_prompt" in runtime["prompt_sources"]
        assert "runtime.assembled" in runtime["prompt_sources"]


# ===========================================================================
# POST /v1/runs/{run_id}/approvals/{approval_id}
# ===========================================================================


class TestRunApproval:
    """Approval decision endpoint: once, session, always, deny."""

    @pytest.fixture
    def approval_adapter(self):
        """Adapter with pre-populated approval session keys, simulating a pending approval."""
        adapter = _make_adapter()
        adapter._run_approval_session_keys["run-1"] = "session-key-abc"
        adapter._run_approval_ids[("run-1", "approval-1")] = "session-key-abc"
        adapter._approval_session_map["approval-1"] = "session-key-abc"
        return adapter

    @pytest.mark.asyncio
    async def test_auth_required(self, auth_adapter):
        """Without auth header, return 401."""
        auth_adapter._run_approval_session_keys["run-1"] = "session-key"
        auth_adapter._run_approval_ids[("run-1", "approval-1")] = "session-key"
        app = _create_approval_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/v1/runs/run-1/approvals/approval-1", json={"decision": "once"})
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_auth_passes_with_valid_key(self, auth_adapter):
        """Valid Bearer token passes auth."""
        auth_adapter._run_approval_session_keys["run-1"] = "session-key"
        auth_adapter._run_approval_ids[("run-1", "approval-1")] = "session-key"
        app = _create_approval_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("tools.approval.has_blocking_approval", return_value=True), \
                 patch("tools.approval.resolve_gateway_approval", return_value=1):
                resp = await cli.post(
                    "/v1/runs/run-1/approvals/approval-1",
                    json={"decision": "once"},
                    headers={"Authorization": "Bearer sk-secret"},
                )
                assert resp.status == 200

    @pytest.mark.asyncio
    async def test_unknown_run_returns_404(self, adapter):
        """Run ID not in _run_approval_session_keys returns 404."""
        app = _create_approval_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs/unknown-run/approvals/approval-1",
                json={"decision": "once"},
            )
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_unknown_approval_returns_404(self, adapter):
        """Approval ID not in _run_approval_ids returns 404."""
        adapter._run_approval_session_keys["run-1"] = "session-key"
        app = _create_approval_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs/run-1/approvals/bogus-approval",
                json={"decision": "once"},
            )
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, approval_adapter):
        """Non-JSON body returns 400."""
        app = _create_approval_app(approval_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs/run-1/approvals/approval-1",
                data=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_decision_returns_400(self, approval_adapter):
        """Decision value not in (once, session, always, deny) returns 400."""
        app = _create_approval_app(approval_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/runs/run-1/approvals/approval-1",
                json={"decision": "maybe"},
            )
            assert resp.status == 400
            body = await resp.json()
            assert "invalid" in body.get("error", {}).get("code", "")

    @pytest.mark.asyncio
    async def test_no_pending_approval_returns_409(self, approval_adapter):
        """When has_blocking_approval returns False, return 409."""
        app = _create_approval_app(approval_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("tools.approval.has_blocking_approval", return_value=False):
                resp = await cli.post(
                    "/v1/runs/run-1/approvals/approval-1",
                    json={"decision": "once"},
                )
                assert resp.status == 409

    @pytest.mark.asyncio
    async def test_resolve_returns_zero_returns_409(self, approval_adapter):
        """When resolve_gateway_approval returns 0, return 409."""
        app = _create_approval_app(approval_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("tools.approval.has_blocking_approval", return_value=True), \
                 patch("tools.approval.resolve_gateway_approval", return_value=0):
                resp = await cli.post(
                    "/v1/runs/run-1/approvals/approval-1",
                    json={"decision": "once"},
                )
                assert resp.status == 409

    @pytest.mark.parametrize("decision", ["once", "session", "always", "deny"])
    @pytest.mark.asyncio
    async def test_all_valid_decisions_succeed(self, approval_adapter, decision):
        """All four valid decisions return 200 with status=processed."""
        app = _create_approval_app(approval_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("tools.approval.has_blocking_approval", return_value=True), \
                 patch("tools.approval.resolve_gateway_approval", return_value=1):
                resp = await cli.post(
                    "/v1/runs/run-1/approvals/approval-1",
                    json={"decision": decision},
                )
                assert resp.status == 200
                body = await resp.json()
                assert body["status"] == "processed"
                assert body["decision"] == decision
                assert body["run_id"] == "run-1"

    @pytest.mark.asyncio
    async def test_cleans_up_mappings_after_success(self, approval_adapter):
        """After successful approval, _run_approval_ids and _approval_session_map are cleaned."""
        app = _create_approval_app(approval_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("tools.approval.has_blocking_approval", return_value=True), \
                 patch("tools.approval.resolve_gateway_approval", return_value=1):
                resp = await cli.post(
                    "/v1/runs/run-1/approvals/approval-1",
                    json={"decision": "once"},
                )
                assert resp.status == 200
                assert ("run-1", "approval-1") not in approval_adapter._run_approval_ids
                assert "approval-1" not in approval_adapter._approval_session_map
                assert "run-1" in approval_adapter._run_approval_session_keys

    @pytest.mark.asyncio
    async def test_cancels_expire_timer(self, approval_adapter):
        """The auto-expire timer is cancelled when user responds."""
        timer = MagicMock()
        approval_adapter._approval_timeout_handles[("run-1", "approval-1")] = timer

        app = _create_approval_app(approval_adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("tools.approval.has_blocking_approval", return_value=True), \
                 patch("tools.approval.resolve_gateway_approval", return_value=1):
                resp = await cli.post(
                    "/v1/runs/run-1/approvals/approval-1",
                    json={"decision": "once"},
                )
                assert resp.status == 200
                timer.cancel.assert_called_once()
                assert ("run-1", "approval-1") not in approval_adapter._approval_timeout_handles


# ===========================================================================
# POST /api/jobs — create with origin
# ===========================================================================


class TestCreateJob:
    """Cron job creation with origin for web delivery routing."""

    def _create_jobs_app(self, adapter):
        mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
        app = web.Application(middlewares=mws)
        app["api_server_adapter"] = adapter
        app.router.add_post("/api/jobs", adapter._handle_create_job)
        # Patch _CRON_AVAILABLE so the handler doesn't 400
        adapter._check_jobs_available = MagicMock(return_value=None)
        return app

    @pytest.mark.asyncio
    async def test_create_job_without_origin_succeeds(self, adapter):
        """Creating a job without origin is still valid (backward compat)."""
        app = self._create_jobs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("gateway.platforms.api_server._cron_create", MagicMock(return_value={"id": "abc123", "deliver": "local"})) as mock_create:
                resp = await cli.post("/api/jobs", json={
                    "name": "test-job",
                    "schedule": "every 10m",
                    "prompt": "Hello",
                })
                assert resp.status == 200
                assert mock_create.called

    @pytest.mark.asyncio
    async def test_create_job_with_origin_passes_it_through(self, adapter):
        """When origin is provided, it is forwarded to _cron_create."""
        app = self._create_jobs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("gateway.platforms.api_server._cron_create", MagicMock(return_value={"id": "abc456"})) as mock_create:
                resp = await cli.post("/api/jobs", json={
                    "name": "web-job",
                    "schedule": "every 10m",
                    "prompt": "test",
                    "deliver": "web",
                    "origin": {"platform": "web", "chat_id": "42"},
                })
                assert resp.status == 200
                mock_create.assert_called_once()
                kwargs = mock_create.call_args.kwargs
                assert kwargs.get("origin") == {"platform": "web", "chat_id": "42"}

    @pytest.mark.asyncio
    async def test_create_job_with_origin_but_without_deliver(self, adapter):
        """Origin is optional — null origin is acceptable."""
        app = self._create_jobs_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("gateway.platforms.api_server._cron_create", MagicMock(return_value={"id": "abc789"})) as mock_create:
                resp = await cli.post("/api/jobs", json={
                    "name": "test",
                    "schedule": "every 5m",
                    "prompt": "test",
                })
                assert resp.status == 200
                kwargs = mock_create.call_args.kwargs
                assert kwargs.get("origin") is None


# ===========================================================================
# POST /api/jobs/{job_id}/run — trigger with deliver/origin overrides
# ===========================================================================


class TestTriggerJob:
    """One-shot trigger with permanent deliver/origin overrides for web delivery."""

    def _create_run_job_app(self, adapter):
        mws = [mw for mw in (cors_middleware, security_headers_middleware) if mw is not None]
        app = web.Application(middlewares=mws)
        app["api_server_adapter"] = adapter
        app.router.add_post("/api/jobs/{job_id}/run", adapter._handle_run_job)
        adapter._check_jobs_available = MagicMock(return_value=None)
        return app

    @pytest.mark.asyncio
    async def test_trigger_job_without_body_succeeds(self, adapter):
        """Triggering without body still works (backward compat) — no update call."""
        app = self._create_run_job_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("gateway.platforms.api_server._cron_trigger", MagicMock(return_value={"id": "abc123def456", "deliver": "local"})) as mock_trigger, \
                 patch("gateway.platforms.api_server._cron_update", MagicMock()) as mock_update:
                resp = await cli.post("/api/jobs/abc123def456/run")
                assert resp.status == 200
                mock_trigger.assert_called_once_with("abc123def456")
                mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_job_with_deliver_override(self, adapter):
        """deliver override calls update_job first, then trigger."""
        app = self._create_run_job_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("gateway.platforms.api_server._cron_trigger", MagicMock(return_value={"id": "abc123def456"})) as mock_trigger, \
                 patch("gateway.platforms.api_server._cron_update", MagicMock(return_value={"id": "abc123def456"})) as mock_update:
                resp = await cli.post("/api/jobs/abc123def456/run", json={"deliver": "web"})
                assert resp.status == 200
                mock_update.assert_called_once_with("abc123def456", {"deliver": "web"})
                mock_trigger.assert_called_once_with("abc123def456")

    @pytest.mark.asyncio
    async def test_trigger_job_with_origin_override(self, adapter):
        """origin override calls update_job first, then trigger."""
        app = self._create_run_job_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("gateway.platforms.api_server._cron_trigger", MagicMock(return_value={"id": "abc123def456"})) as mock_trigger, \
                 patch("gateway.platforms.api_server._cron_update", MagicMock(return_value={"id": "abc123def456"})) as mock_update:
                resp = await cli.post("/api/jobs/abc123def456/run", json={"origin": {"platform": "web", "chat_id": "42"}})
                assert resp.status == 200
                mock_update.assert_called_once_with("abc123def456", {"origin": {"platform": "web", "chat_id": "42"}})
                mock_trigger.assert_called_once_with("abc123def456")

    @pytest.mark.asyncio
    async def test_trigger_job_with_both_overrides(self, adapter):
        """Both deliver and origin overrides call update_job once, then trigger."""
        app = self._create_run_job_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("gateway.platforms.api_server._cron_trigger", MagicMock(return_value={"id": "abc123def456"})) as mock_trigger, \
                 patch("gateway.platforms.api_server._cron_update", MagicMock(return_value={"id": "abc123def456"})) as mock_update:
                resp = await cli.post("/api/jobs/abc123def456/run", json={
                    "deliver": "web",
                    "origin": {"platform": "web", "chat_id": "99"},
                })
                assert resp.status == 200
                mock_update.assert_called_once_with("abc123def456", {
                    "deliver": "web",
                    "origin": {"platform": "web", "chat_id": "99"},
                })
                mock_trigger.assert_called_once_with("abc123def456")

    @pytest.mark.asyncio
    async def test_trigger_job_not_found_returns_404(self, adapter):
        """When _cron_trigger returns None, return 404."""
        app = self._create_run_job_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch("gateway.platforms.api_server._cron_trigger", MagicMock(return_value=None)):
                resp = await cli.post("/api/jobs/abc123def456/run", json={"deliver": "web"})
                assert resp.status == 404

    @pytest.mark.asyncio
    async def test_trigger_job_auth_required(self, auth_adapter):
        """When API key is configured, reject without auth."""
        app = self._create_run_job_app(auth_adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post("/api/jobs/abc123def456/run", json={"deliver": "web"})
            assert resp.status == 401
