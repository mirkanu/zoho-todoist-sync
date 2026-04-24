"""Unit tests for app.webhooks — Zoho handler, router path registration, lifespan wiring.

Covers:
  - SYNC-4: Zoho notification-only webhook (enqueue, 400 validation, ids normalisation)
  - INFRA-1: ArqRedis pool wired on app.state.redis at startup
  - INFRA-4: Both /webhooks/zoho and /webhooks/todoist routes registered
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
# Todoist stub — permissive (Plan 02 tightens to 401)
# ---------------------------------------------------------------------------

def test_todoist_webhook_stub_returns_200_without_side_effects(webhook_client):
    """Todoist stub returns 200 (Plan 01) or 401 (Plan 02 HMAC); no side effects."""
    with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
        resp = webhook_client.post(
            "/webhooks/todoist",
            json={"event_name": "item:completed", "event_data": {"id": "12345"}},
        )
    assert resp.status_code in (200, 401)
    mock_enqueue.assert_not_awaited()
