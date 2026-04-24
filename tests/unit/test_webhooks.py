"""Unit tests for app.webhooks — Zoho handler, router path registration, lifespan wiring.

Covers:
  - SYNC-4: Zoho notification-only webhook (enqueue, 400 validation, ids normalisation)
  - INFRA-1: ArqRedis pool wired on app.state.redis at startup
  - INFRA-4: Both /webhooks/zoho and /webhooks/todoist routes registered
  - INFRA-1 / SYNC-8 / LOOP-5 / EDGE-7 / EDGE-8: Todoist HMAC + event dispatch (Plan 02)
"""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_settings_cache(complete_env):
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def webhook_client(complete_env):
    """TestClient with app.state.redis and app.state.session_factory stubbed.

    Uses the real app instance from app.main but bypasses the real lifespan
    by skipping startup (TestClient's context manager is NOT entered).
    """
    from app.main import app
    app.state.redis = AsyncMock()
    app.state.session_factory = MagicMock()
    # TestClient with raise_server_exceptions=True surfaces real errors.
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helper: HMAC signature for Todoist webhook requests
# ---------------------------------------------------------------------------

def _make_hmac(body: bytes, secret: str = "test-todoist-client-secret") -> str:
    """Compute base64-encoded HMAC-SHA256 over raw body bytes."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


# ---------------------------------------------------------------------------
# Helper: mock session_factory returning a given zoho_id (or None)
# ---------------------------------------------------------------------------

def _mock_session_factory_returning(zoho_id: str | None):
    """Return a mock session_factory whose session.execute returns zoho_id via scalar_one_or_none."""
    sess = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=zoho_id)
    sess.execute = AsyncMock(return_value=result)
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__ = AsyncMock(return_value=sess)
    ctx_mgr.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=ctx_mgr)
    return factory


# ---------------------------------------------------------------------------
# SYNC-4: Zoho handler enqueues on valid payload
# ---------------------------------------------------------------------------

def test_zoho_webhook_enqueues(webhook_client):
    """Valid POST enqueues sync_task with correct args."""
    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/zoho",
            json={"module": "Tasks", "ids": ["4567890"]},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_enqueue.assert_awaited_once()
    args, kwargs = mock_enqueue.call_args
    # args[1] is zoho_task_id
    assert args[1] == "4567890"
    assert kwargs.get("defer_secs") == 2


# ---------------------------------------------------------------------------
# SYNC-4: Validation — missing module or ids returns 400
# ---------------------------------------------------------------------------

def test_zoho_missing_module_returns_400(webhook_client):
    """Missing module → 400; missing ids → 400; empty ids → 400."""
    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        # Missing module
        resp = webhook_client.post("/webhooks/zoho", json={"ids": ["123"]})
        assert resp.status_code == 400

        # Missing ids
        resp2 = webhook_client.post("/webhooks/zoho", json={"module": "Tasks"})
        assert resp2.status_code == 400

        # Empty ids list
        resp3 = webhook_client.post("/webhooks/zoho", json={"module": "Tasks", "ids": []})
        assert resp3.status_code == 400

        mock_enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# SYNC-4 edge: Invalid JSON returns 400
# ---------------------------------------------------------------------------

def test_zoho_invalid_json_returns_400(webhook_client):
    """Non-JSON body or malformed JSON → 400."""
    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        # Plain string body with no content-type
        resp = webhook_client.post(
            "/webhooks/zoho",
            content=b"not-json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 400

        # Malformed JSON
        resp2 = webhook_client.post(
            "/webhooks/zoho",
            content=b"{broken json",
            headers={"Content-Type": "application/json"},
        )
        assert resp2.status_code == 400

        mock_enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# A3 assumption: ids delivered as bare string is tolerated
# ---------------------------------------------------------------------------

def test_zoho_ids_as_bare_string_is_tolerated(webhook_client):
    """ids as bare string (not a list) is accepted and normalised."""
    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/zoho",
            json={"module": "Tasks", "ids": "789"},
        )
    assert resp.status_code == 200
    args, kwargs = mock_enqueue.call_args
    assert args[1] == "789"


# ---------------------------------------------------------------------------
# ids list with integer element — stringified
# ---------------------------------------------------------------------------

def test_zoho_ids_takes_first_element_and_stringifies(webhook_client):
    """ids list with integer element is stringified."""
    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/zoho",
            json={"module": "Tasks", "ids": [4567890]},
        )
    assert resp.status_code == 200
    args, kwargs = mock_enqueue.call_args
    assert args[1] == "4567890"


# ---------------------------------------------------------------------------
# INFRA-4: Route registration check
# ---------------------------------------------------------------------------

def test_router_paths():
    """Both /webhooks/zoho and /webhooks/todoist are registered as POST routes."""
    from app.main import app
    paths = [r.path for r in app.router.routes]
    assert "/webhooks/zoho" in paths, f"Missing /webhooks/zoho in {paths}"
    assert "/webhooks/todoist" in paths, f"Missing /webhooks/todoist in {paths}"

    # Confirm they accept POST
    for route in app.router.routes:
        if getattr(route, "path", None) in ("/webhooks/zoho", "/webhooks/todoist"):
            assert "POST" in route.methods, f"{route.path} missing POST method"


# ---------------------------------------------------------------------------
# Router exports check
# ---------------------------------------------------------------------------

def test_webhooks_router_exports_router():
    """app.webhooks.router exports an APIRouter instance."""
    from app.webhooks.router import router
    assert isinstance(router, APIRouter)


# ---------------------------------------------------------------------------
# Package importability
# ---------------------------------------------------------------------------

def test_webhooks_package_is_importable():
    """app.webhooks can be imported without error."""
    import importlib
    importlib.import_module("app.webhooks")


# ---------------------------------------------------------------------------
# INFRA-1: Lifespan wires ArqRedis pool + session_factory on app.state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lifespan_wires_arq_redis_and_session_factory(monkeypatch, complete_env):
    """Lifespan creates ArqRedis pool, stores on app.state.redis, stores session_factory."""
    from app.core.config import get_settings as _gs
    _gs.cache_clear()

    # Stub engine
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    monkeypatch.setattr("app.main.create_async_engine", lambda *a, **k: fake_engine)

    # Stub session_factory
    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None
    fake_session_factory = MagicMock(return_value=fake_session)
    monkeypatch.setattr("app.main.async_sessionmaker", lambda *a, **k: fake_session_factory)

    # Stub token loading
    monkeypatch.setattr(
        "app.main.load_token_from_kv",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        "app.main.refresh_access_token",
        AsyncMock(return_value=("tok", datetime.now(timezone.utc) + timedelta(hours=1))),
    )
    monkeypatch.setattr("app.main.upsert_kv", AsyncMock())

    # Stub ZohoClient
    meta_return = {
        "todoist_task_id_api_name": "Todoist_Task_ID",
        "status_picklist_values": ["Completed"],
    }
    class FakeZohoClient:
        def __init__(self, access_token): pass
        async def get_fields_metadata(self, module="Tasks"):
            return meta_return
    monkeypatch.setattr("app.main.ZohoClient", FakeZohoClient)

    # Stub TodoistClient
    class FakeTodoistClient:
        def __init__(self, api_token): pass
        async def close(self): pass
    monkeypatch.setattr("app.main.TodoistClient", FakeTodoistClient)

    # Stub startup_sync
    monkeypatch.setattr("app.main.startup_sync", AsyncMock())

    # Stub proactive_refresh_loop + create_task
    import asyncio

    async def fake_loop(ts, sf):
        await asyncio.sleep(3600)
    monkeypatch.setattr("app.main.proactive_refresh_loop", fake_loop)

    # Stub ArqRedis pool
    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()
    mock_create_pool = AsyncMock(return_value=mock_redis)
    monkeypatch.setattr("app.main.create_pool", mock_create_pool)

    # Run the lifespan
    from fastapi import FastAPI
    from app.main import lifespan

    test_app = FastAPI()
    async with lifespan(test_app):
        # During startup
        assert test_app.state.redis is mock_redis
        assert test_app.state.session_factory is fake_session_factory
        mock_create_pool.assert_awaited_once()

    # After shutdown
    mock_redis.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Todoist — Plan 02: HMAC enforcement (replaces permissive Plan 01 stub test)
# ---------------------------------------------------------------------------

def test_todoist_webhook_without_hmac_returns_401_post_plan02(webhook_client):
    """After Plan 02, a body with no HMAC returns 401 (not 200).

    Replaces the Plan 01 permissive stub test which allowed (200, 401).
    """
    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            json={"event_name": "item:completed", "event_data": {"id": "12345"}},
        )
    assert resp.status_code == 401
    mock_enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# INFRA-1 / T-06-09: HMAC verification
# ---------------------------------------------------------------------------

def test_todoist_invalid_hmac_returns_401(webhook_client):
    """Bad HMAC header → 401; no enqueue; no DB lookup."""
    from app.main import app
    session_factory = _mock_session_factory_returning("Z1")
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:updated",
        "event_data": {"id": "T1", "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={
                "X-Todoist-Hmac-SHA256": "definitely-not-valid-base64-hmac==",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 401
    mock_enqueue.assert_not_awaited()
    session_factory.assert_not_called()


def test_todoist_no_hmac_header_returns_401(webhook_client):
    """No X-Todoist-Hmac-SHA256 header at all → 401."""
    raw_body = json.dumps({
        "event_name": "item:updated",
        "event_data": {"id": "T1", "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 401
    mock_enqueue.assert_not_awaited()


def test_todoist_hmac_uses_raw_body_not_parsed_json(webhook_client):
    """HMAC is computed over raw wire bytes, NOT re-serialised parsed JSON (Pitfall 1).

    The raw body has non-default whitespace that json.dumps would not reproduce.
    If the handler re-serialises, the HMAC will not match and the test sees 401.
    Signing the exact wire bytes and getting 200 proves the handler used raw bytes.
    """
    from app.main import app
    session_factory = _mock_session_factory_returning("Z-raw-body")
    app.state.session_factory = session_factory

    # Non-default whitespace after colons/commas — json.dumps won't produce this.
    raw_body = b'{  "event_name": "item:updated",   "event_data":{"id":"1","project_id":"6gCPcWwM392GhXQh"}}'
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={
                "X-Todoist-Hmac-SHA256": sig,
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200, (
        "Expected 200 (HMAC matched raw bytes). If 401, handler re-serialised JSON."
    )


def test_todoist_hmac_compare_uses_compare_digest():
    """Code-level invariant: hmac.compare_digest used; == comparison forbidden (T-06-10)."""
    import pathlib
    src = pathlib.Path("app/webhooks/router.py").read_text()
    assert "hmac.compare_digest" in src, "Todoist HMAC must use hmac.compare_digest"
    # Anti-regression: forbid == comparison on hmac/expected identifiers.
    forbidden = [
        "expected == received", "received == expected",
        "expected==received", "received==expected",
    ]
    for pattern in forbidden:
        assert pattern not in src, f"Found timing-vulnerable comparison: {pattern}"


# ---------------------------------------------------------------------------
# SYNC-8: item:added without footer discarded
# ---------------------------------------------------------------------------

def test_todoist_item_added_no_footer_discarded(webhook_client):
    """item:added with no [zoho:ID] footer → 200, no enqueue, no DB lookup (SYNC-8)."""
    from app.main import app
    session_factory = _mock_session_factory_returning(None)
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:added",
        "event_data": {"id": "999", "project_id": "6gCPcWwM392GhXQh", "description": "no footer here"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()
    session_factory.assert_not_called()


# ---------------------------------------------------------------------------
# LOOP-5: item:added with footer enqueues (skips DB lookup)
# ---------------------------------------------------------------------------

def test_todoist_item_added_with_footer_enqueues(webhook_client):
    """item:added with [zoho:ID] footer → enqueue with zoho_id; no DB lookup (LOOP-5)."""
    from app.main import app
    session_factory = _mock_session_factory_returning(None)
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:added",
        "event_data": {
            "id": "999",
            "project_id": "6gCPcWwM392GhXQh",
            "description": "body\n\n---\n[zoho:4567890]",
        },
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_awaited_once()
    args, kwargs = mock_enqueue.call_args
    assert args[1] == "4567890"
    assert kwargs.get("defer_secs") == 0
    session_factory.assert_not_called()


# ---------------------------------------------------------------------------
# item:updated, item:completed, item:uncompleted — DB lookup + enqueue
# ---------------------------------------------------------------------------

def test_todoist_item_updated_enqueues(webhook_client):
    """item:updated → DB lookup → enqueue(zoho_id, defer_secs=0)."""
    from app.main import app
    session_factory = _mock_session_factory_returning("Z1")
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:updated",
        "event_data": {"id": "T1", "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        response = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={
                "X-Todoist-Hmac-SHA256": sig,
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 200
    mock_enqueue.assert_awaited_once()
    args, kwargs = mock_enqueue.call_args
    assert args[1] == "Z1"
    assert kwargs.get("defer_secs") == 0
    session_factory.assert_called_once()


def test_todoist_item_completed_enqueues(webhook_client):
    """item:completed → DB lookup → enqueue (EDGE-7: completion propagates to Zoho)."""
    from app.main import app
    session_factory = _mock_session_factory_returning("Z2")
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:completed",
        "event_data": {"id": "T2", "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_awaited_once()
    args, kwargs = mock_enqueue.call_args
    assert args[1] == "Z2"
    assert kwargs.get("defer_secs") == 0


def test_todoist_item_uncompleted_enqueues(webhook_client):
    """item:uncompleted → DB lookup → enqueue."""
    from app.main import app
    session_factory = _mock_session_factory_returning("Z3")
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:uncompleted",
        "event_data": {"id": "T3", "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_awaited_once()
    args, kwargs = mock_enqueue.call_args
    assert args[1] == "Z3"
    assert kwargs.get("defer_secs") == 0


# ---------------------------------------------------------------------------
# item:deleted
# ---------------------------------------------------------------------------

def test_todoist_item_deleted_enqueues_when_sync_state_exists(webhook_client):
    """item:deleted with sync_state row → enqueue(zoho_id, defer_secs=0)."""
    from app.main import app
    session_factory = _mock_session_factory_returning("Z4")
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:deleted",
        "event_data": {"id": "T4", "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_awaited_once()
    args, kwargs = mock_enqueue.call_args
    assert args[1] == "Z4"
    assert kwargs.get("defer_secs") == 0


def test_todoist_item_deleted_no_sync_state_logs_and_returns_200(webhook_client):
    """item:deleted with no sync_state row → 200, no enqueue."""
    from app.main import app
    session_factory = _mock_session_factory_returning(None)
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:deleted",
        "event_data": {"id": "T5", "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# EDGE-8: Missing sync_state for updated/completed/uncompleted
# ---------------------------------------------------------------------------

def test_todoist_missing_footer_on_synced_task(webhook_client):
    """item:updated with no sync_state row → 200, no enqueue (EDGE-8 proxy)."""
    from app.main import app
    session_factory = _mock_session_factory_returning(None)
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:updated",
        "event_data": {"id": "T-no-state", "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# Wrong project_id → discard without DB lookup
# ---------------------------------------------------------------------------

def test_todoist_wrong_project_id_discarded(webhook_client):
    """event_data.project_id != configured project → 200, no enqueue, no DB."""
    from app.main import app
    session_factory = _mock_session_factory_returning("Z-other")
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:updated",
        "event_data": {"id": "T-other", "project_id": "OTHER_PROJECT"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()
    session_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Unknown event_name → 200 (DEBUG log, no action)
# ---------------------------------------------------------------------------

def test_todoist_unknown_event_name_returns_200(webhook_client):
    """Unknown event_name → 200, no enqueue."""
    raw_body = json.dumps({
        "event_name": "item:changed_somehow_new",
        "event_data": {"id": "T-unknown", "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# A2: event_data.id as int is coerced via str() (T-06-14)
# ---------------------------------------------------------------------------

def test_todoist_event_data_id_as_int_tolerated(webhook_client):
    """event_data.id as int (not string) → str() coercion → enqueue works (T-06-14)."""
    from app.main import app
    session_factory = _mock_session_factory_returning("Z-int")
    app.state.session_factory = session_factory

    raw_body = json.dumps({
        "event_name": "item:updated",
        "event_data": {"id": 12345, "project_id": "6gCPcWwM392GhXQh"},
    }).encode("utf-8")
    sig = _make_hmac(raw_body)

    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            content=raw_body,
            headers={"X-Todoist-Hmac-SHA256": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    mock_enqueue.assert_awaited_once()
    args, kwargs = mock_enqueue.call_args
    assert args[1] == "Z-int"
    assert kwargs.get("defer_secs") == 0
