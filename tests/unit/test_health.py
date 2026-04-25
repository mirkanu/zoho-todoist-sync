"""Unit tests for app.health.router — GET /health endpoint.

Covers OBS-1 response shape, D-10 status logic, HTTP code mapping,
no-live-API guard, zcard/scan_iter pattern, and last_sync.source contract.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.health.router import router


# ---------------------------------------------------------------------------
# Session factory helper — multi-execute with side_effect
# ---------------------------------------------------------------------------

def _make_session_factory(scalar_returns: list, scalar_or_none_returns: list | None = None):
    """Build a session_factory mock where each execute() call returns the next scalar value.

    scalar_returns: list of values for scalar_one() calls (errors, echoes, syncs, active_tasks)
    scalar_or_none_returns: list of values for scalar_one_or_none() calls (last_event, reconciler_kv)
                             If None, reuse scalar_returns for both methods.
    """
    sess = AsyncMock()
    results = []
    all_returns = list(scalar_returns)  # copy
    for s in all_returns:
        r = MagicMock()
        r.scalar_one = MagicMock(return_value=s)
        r.scalar_one_or_none = MagicMock(return_value=s)
        results.append(r)

    # The last two execute() calls are for last_event and reconciler_kv
    # (scalar_one_or_none). Override if separate list provided.
    if scalar_or_none_returns is not None:
        for i, s in enumerate(scalar_or_none_returns):
            r = MagicMock()
            r.scalar_one = MagicMock(return_value=s)
            r.scalar_one_or_none = MagicMock(return_value=s)
            results.append(r)

    sess.execute = AsyncMock(side_effect=results)
    sess.commit = AsyncMock()
    ctx = AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    )
    return MagicMock(return_value=ctx)


# ---------------------------------------------------------------------------
# Redis mock helper
# ---------------------------------------------------------------------------

async def _empty_async_iter():
    """Async iterator that yields nothing (empty scan_iter)."""
    return
    yield  # make it a generator


def _make_redis_mock(queue_depth: int = 0, in_progress_count: int = 0):
    """Build a redis mock with zcard and scan_iter configured."""
    mock_redis = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=queue_depth)

    # scan_iter must return an async iterable
    async def _scan_iter(*args, **kwargs):
        for _ in range(in_progress_count):
            yield f"arq:in-progress:job-{_}"

    mock_redis.scan_iter = _scan_iter
    return mock_redis


# ---------------------------------------------------------------------------
# Test app builder
# ---------------------------------------------------------------------------

def _make_test_app(mock_redis, mock_session_factory, extra_state: dict | None = None):
    """Build a minimal FastAPI app with only the health router mounted."""
    app = FastAPI()
    app.include_router(router)
    app.state.redis = mock_redis
    app.state.session_factory = mock_session_factory
    # Set zoho_client and todoist_client to plain MagicMock so no-live-API test can assert
    app.state.zoho_client = MagicMock()
    app.state.todoist_client = MagicMock()
    if extra_state:
        for k, v in extra_state.items():
            setattr(app.state, k, v)
    return app


# ---------------------------------------------------------------------------
# T1: test_health_ok_shape
# ---------------------------------------------------------------------------

def test_health_ok_shape():
    """status='ok' with fresh reconciler + errors_24h<=10 + failed==0; response has correct shape; HTTP 200."""
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_redis = _make_redis_mock(queue_depth=2, in_progress_count=1)

    # Execute calls (in order): errors_24h=0, echoes_24h=3, syncs_24h=5, active_tasks=10
    # Then: last_event=None, reconciler_kv=now_iso
    mock_sf = _make_session_factory(
        scalar_returns=[0, 3, 5, 10],
        scalar_or_none_returns=[None, now_iso],
    )

    app = _make_test_app(mock_redis, mock_sf)
    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()

    # Top-level keys
    assert "status" in body
    assert "last_sync" in body
    assert "queue" in body
    assert "errors_24h" in body
    assert "echoes_suppressed_24h" in body
    assert "syncs_24h" in body
    assert "active_tasks" in body
    assert "reconciler" in body

    # queue sub-keys
    assert "depth" in body["queue"]
    assert "in_progress" in body["queue"]
    assert "failed" in body["queue"]

    # reconciler sub-key
    assert "last_run" in body["reconciler"]

    assert body["status"] == "ok"
    assert body["errors_24h"] == 0
    assert body["echoes_suppressed_24h"] == 3
    assert body["syncs_24h"] == 5
    assert body["active_tasks"] == 10
    assert body["queue"]["depth"] == 2
    assert body["queue"]["in_progress"] == 1


# ---------------------------------------------------------------------------
# T2: test_health_degraded_when_errors_over_10
# ---------------------------------------------------------------------------

def test_health_degraded_when_errors_over_10():
    """errors_24h=11 → status='degraded', HTTP 200."""
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_redis = _make_redis_mock()

    mock_sf = _make_session_factory(
        scalar_returns=[11, 0, 0, 0],
        scalar_or_none_returns=[None, now_iso],
    )

    app = _make_test_app(mock_redis, mock_sf)
    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


# ---------------------------------------------------------------------------
# T3: test_health_error_stale_reconciler
# ---------------------------------------------------------------------------

def test_health_error_stale_reconciler():
    """reconciler_last_run is 31 minutes old → status='error', HTTP 503."""
    from datetime import timedelta
    stale_iso = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    mock_redis = _make_redis_mock()

    mock_sf = _make_session_factory(
        scalar_returns=[0, 0, 0, 0],
        scalar_or_none_returns=[None, stale_iso],
    )

    app = _make_test_app(mock_redis, mock_sf)
    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["status"] == "error"


# ---------------------------------------------------------------------------
# T4: test_health_error_missing_reconciler
# ---------------------------------------------------------------------------

def test_health_error_missing_reconciler():
    """kv_store has no reconciler_last_run row → status='error', HTTP 503."""
    mock_redis = _make_redis_mock()

    mock_sf = _make_session_factory(
        scalar_returns=[0, 0, 0, 0],
        scalar_or_none_returns=[None, None],  # reconciler_kv is None
    )

    app = _make_test_app(mock_redis, mock_sf)
    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["status"] == "error"


# ---------------------------------------------------------------------------
# T5: test_health_error_failed_jobs
# ---------------------------------------------------------------------------

def test_health_error_failed_jobs():
    """queue.failed (errors_24h proxy) reflects errors_24h; stale reconciler triggers HTTP 503.

    Verifies that queue.failed in the response body equals errors_24h (the proxy value),
    and that an error condition (stale reconciler) produces HTTP 503.
    """
    from datetime import timedelta
    stale_iso = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    mock_redis = _make_redis_mock()

    # errors_24h=5 → queue.failed=5>0 in response body
    mock_sf = _make_session_factory(
        scalar_returns=[5, 0, 0, 0],  # errors_24h=5 → queue.failed proxy=5
        scalar_or_none_returns=[None, stale_iso],  # stale reconciler → error
    )

    app = _make_test_app(mock_redis, mock_sf)
    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["status"] == "error"
    assert resp.json()["queue"]["failed"] > 0  # proxy value appears in response body


# ---------------------------------------------------------------------------
# T6: test_health_no_live_api_calls
# ---------------------------------------------------------------------------

def test_health_no_live_api_calls():
    """ZohoClient and TodoistClient are NOT called from the health request path."""
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_redis = _make_redis_mock()

    mock_sf = _make_session_factory(
        scalar_returns=[0, 0, 0, 0],
        scalar_or_none_returns=[None, now_iso],
    )

    mock_zoho = MagicMock()
    mock_todoist = MagicMock()

    app = _make_test_app(mock_redis, mock_sf, extra_state={
        "zoho_client": mock_zoho,
        "todoist_client": mock_todoist,
    })
    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 200
    # Neither zoho_client nor todoist_client should have been called
    assert mock_zoho.method_calls == [], f"ZohoClient was called: {mock_zoho.method_calls}"
    assert mock_todoist.method_calls == [], f"TodoistClient was called: {mock_todoist.method_calls}"


# ---------------------------------------------------------------------------
# T7: test_health_uses_zcard_not_keys
# ---------------------------------------------------------------------------

def test_health_uses_zcard_not_keys():
    """redis.zcard called with 'arq:queue'; redis.scan_iter called (NOT redis.keys)."""
    from arq.constants import default_queue_name

    now_iso = datetime.now(timezone.utc).isoformat()

    zcard_calls = []
    scan_iter_calls = []

    class TrackingRedis:
        async def zcard(self, key):
            zcard_calls.append(key)
            return 0

        async def scan_iter(self, *args, **kwargs):
            scan_iter_calls.append((args, kwargs))
            return
            yield  # make it an async generator

        # Ensure no `keys` method was called (would raise if called)
        @property
        def keys(self):
            raise AssertionError("redis.keys must NOT be called — use scan_iter instead")

    mock_redis = TrackingRedis()
    mock_sf = _make_session_factory(
        scalar_returns=[0, 0, 0, 0],
        scalar_or_none_returns=[None, now_iso],
    )

    app = _make_test_app(mock_redis, mock_sf)
    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 200
    assert zcard_calls == [default_queue_name], (
        f"Expected zcard('{default_queue_name}'), got: {zcard_calls}"
    )
    assert len(scan_iter_calls) >= 1, "redis.scan_iter was not called"


# ---------------------------------------------------------------------------
# T8: test_health_last_sync_includes_source
# ---------------------------------------------------------------------------

def test_health_last_sync_includes_source():
    """last_sync.source and last_sync.at present when most recent sync event exists."""
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_redis = _make_redis_mock()

    # Simulate a SyncEvent-shaped object for last_event
    last_event = MagicMock()
    last_event.action = "sync"
    last_event.source = "zoho_webhook"
    last_event.created_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    # errors_24h=0, echoes=0, syncs=1, active_tasks=5, then last_event, reconciler_kv
    mock_sf = _make_session_factory(
        scalar_returns=[0, 0, 1, 5],
        scalar_or_none_returns=[last_event, now_iso],
    )

    app = _make_test_app(mock_redis, mock_sf)
    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["last_sync"] is not None
    assert body["last_sync"]["source"] == "zoho_webhook"
    assert "at" in body["last_sync"]
    # at should be a valid ISO 8601 string
    at_val = body["last_sync"]["at"]
    assert "2026" in at_val  # sanity check it's our date
