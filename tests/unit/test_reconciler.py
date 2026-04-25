"""Unit tests for app.worker.reconciler — reconcile_sweep cron. SEED-5, SEED-7."""
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_reconciler_ctx():
    """Build a minimal arq ctx dict for reconcile_sweep (cron, no job_try)."""
    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock(return_value=MagicMock())
    return {
        "redis": mock_redis,
        "session_factory": MagicMock(),
        "zoho_client": AsyncMock(),
        "todoist_client": AsyncMock(),
    }


def _mock_session_factory_with_state(state):
    """Build a session_factory mock where session.execute returns `state` on scalar_one_or_none."""
    sess = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=state)
    result.scalar_one = MagicMock(return_value=state)
    sess.execute = AsyncMock(return_value=result)
    sess.add = MagicMock()
    sess.commit = AsyncMock()
    sess.begin = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    ))
    factory_ctx = AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    )
    factory = MagicMock(return_value=factory_ctx)
    return factory, sess


# ---------------------------------------------------------------------------
# Test 1: Zoho hash mismatch → enqueue_sync called once with correct args
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_zoho_mismatch(complete_env, monkeypatch):
    """Hash mismatch between fresh Zoho record and sync_state.last_hash → enqueue."""
    from app.worker.reconciler import reconcile_sweep

    ctx = _make_reconciler_ctx()

    state = MagicMock()
    state.last_hash = "stale_hash_value"
    factory, sess = _mock_session_factory_with_state(state)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(
        return_value=[{
            "id": "Z1",
            "Subject": "Buy milk",
            "Due_Date": "2026-05-01",
            "Priority": "High",
            "Status": "Not Started",
            "Owner": {"id": "user-123"},
        }]
    )
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=([], "new-token"))

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())

    await reconcile_sweep(ctx)

    assert mock_enqueue.call_count == 1
    call = mock_enqueue.call_args
    assert call.args[0] is ctx["redis"]
    assert call.args[1] == "Z1"
    assert call.kwargs.get("defer_secs", 0) == 0


# ---------------------------------------------------------------------------
# Test 2: Zoho hash matches → enqueue_sync NOT called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_zoho_match_no_enqueue(complete_env, monkeypatch):
    """When fresh Zoho hash equals sync_state.last_hash, enqueue_sync is not called."""
    from app.worker.reconciler import reconcile_sweep
    from app.core.normalise import NormalisedTask
    from app.core.hash import canonical_hash

    ctx = _make_reconciler_ctx()

    norm = NormalisedTask(title="Buy milk", due_date="2026-05-01", priority=3, is_completed=False)
    matching_hash = canonical_hash(norm)
    assert isinstance(matching_hash, str)
    assert len(matching_hash) == 64  # SHA-256 hex

    state = MagicMock()
    state.last_hash = matching_hash
    factory, sess = _mock_session_factory_with_state(state)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(
        return_value=[{
            "id": "Z1",
            "Subject": "Buy milk",
            "Due_Date": "2026-05-01",
            "Priority": "High",
            "Status": "Not Started",
            "Owner": {"id": "user-123"},
        }]
    )
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=([], "new-token"))

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())

    await reconcile_sweep(ctx)

    assert mock_enqueue.call_count == 0


# ---------------------------------------------------------------------------
# Test 3: No sync_state row → treat as mismatch, enqueue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_zoho_no_state_row_enqueues(complete_env, monkeypatch):
    """When no sync_state row exists for a Zoho record, treat as mismatch and enqueue."""
    from app.worker.reconciler import reconcile_sweep

    ctx = _make_reconciler_ctx()

    # scalar_one_or_none returns None (no DB row)
    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(
        return_value=[{
            "id": "Z1",
            "Subject": "New task",
            "Due_Date": None,
            "Priority": None,
            "Status": "Not Started",
            "Owner": {"id": "user-123"},
        }]
    )
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=([], "new-token"))

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())

    await reconcile_sweep(ctx)

    assert mock_enqueue.call_count == 1
    call = mock_enqueue.call_args
    assert call.args[1] == "Z1"
    assert call.kwargs.get("defer_secs", 0) == 0


# ---------------------------------------------------------------------------
# Test 4: Todoist delta items with [zoho:ID] footer → enqueue per item
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_todoist_delta(complete_env, monkeypatch):
    """Todoist delta items with [zoho:ID] footer are enqueued with defer_secs=0."""
    from app.worker.reconciler import reconcile_sweep

    ctx = _make_reconciler_ctx()
    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(return_value=[])
    items = [
        {"id": "T1", "description": "some task\n\n---\n[zoho:100]", "is_deleted": False},
        {"id": "T2", "description": "[zoho:200]", "is_deleted": False},
    ]
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=(items, "new-token"))

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())

    await reconcile_sweep(ctx)

    assert mock_enqueue.call_count == 2
    calls = [c.args for c in mock_enqueue.call_args_list]
    assert calls[0][0] is ctx["redis"]
    assert calls[0][1] == "100"
    assert calls[1][0] is ctx["redis"]
    assert calls[1][1] == "200"
    assert mock_enqueue.call_args_list[0].kwargs.get("defer_secs", 0) == 0
    assert mock_enqueue.call_args_list[1].kwargs.get("defer_secs", 0) == 0


# ---------------------------------------------------------------------------
# Test 5: Todoist items without footer → enqueue_sync NOT called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_todoist_delta_no_footer_skipped(complete_env, monkeypatch):
    """Items without [zoho:ID] footer are skipped; enqueue_sync not called for them."""
    from app.worker.reconciler import reconcile_sweep

    ctx = _make_reconciler_ctx()
    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(return_value=[])
    items = [
        {"id": "T1", "description": "No footer here", "is_deleted": False},
        {"id": "T2", "description": "", "is_deleted": False},
        {"id": "T3", "description": None, "is_deleted": False},
    ]
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=(items, "new-token"))

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())

    await reconcile_sweep(ctx)

    assert mock_enqueue.call_count == 0


# ---------------------------------------------------------------------------
# Test 6: Todoist items with is_deleted=True → enqueue_sync NOT called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_todoist_delta_is_deleted_skipped(complete_env, monkeypatch):
    """Items with is_deleted=True are skipped; enqueue_sync not called for them."""
    from app.worker.reconciler import reconcile_sweep

    ctx = _make_reconciler_ctx()
    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(return_value=[])
    items = [
        {"id": "T1", "description": "[zoho:999]", "is_deleted": True},
    ]
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=(items, "new-token"))

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())

    await reconcile_sweep(ctx)

    assert mock_enqueue.call_count == 0


# ---------------------------------------------------------------------------
# Test 7: sync_token saved after delta fetch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_token_saved(complete_env, monkeypatch):
    """After fetch_sync_delta returns new_token, save_sync_token is called once with it."""
    from app.worker.reconciler import reconcile_sweep

    ctx = _make_reconciler_ctx()
    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(return_value=[])
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=([], "saved-token-abc"))

    mock_save = AsyncMock()
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", mock_save)

    await reconcile_sweep(ctx)

    assert mock_save.call_count == 1
    assert mock_save.call_args.args[1] == "saved-token-abc"


# ---------------------------------------------------------------------------
# Test 8: reconciler_last_run updated with ISO-8601 timestamp + commit called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconciler_last_run_updated(complete_env, monkeypatch):
    """At end of sweep, upsert_kv called with 'reconciler_last_run' and ISO-8601 value."""
    from app.worker.reconciler import reconcile_sweep, KV_RECONCILER_LAST_RUN

    ctx = _make_reconciler_ctx()
    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(return_value=[])
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=([], "tok"))

    mock_upsert = AsyncMock()
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", mock_upsert)
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())

    await reconcile_sweep(ctx)

    assert mock_upsert.call_count == 1
    call_args = mock_upsert.call_args
    assert call_args.args[1] == KV_RECONCILER_LAST_RUN
    ts_value = call_args.args[2]
    assert isinstance(ts_value, str)
    # Must be an ISO-8601 string (parseable by datetime.fromisoformat)
    from datetime import datetime
    parsed = datetime.fromisoformat(ts_value)
    assert parsed is not None
    assert parsed.tzinfo is not None  # must be tz-aware (UTC)
    # session.commit() must have been called after upsert_kv
    assert sess.commit.await_count >= 1


# ---------------------------------------------------------------------------
# Test 9: dedup — enqueue_sync returns None → no exception, sweep continues
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_dedup(complete_env, monkeypatch):
    """When enqueue_sync returns None (duplicate), reconciler does not raise; sweep continues."""
    from app.worker.reconciler import reconcile_sweep

    ctx = _make_reconciler_ctx()
    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(
        return_value=[{
            "id": "Z1",
            "Subject": "Task",
            "Due_Date": None,
            "Priority": None,
            "Status": "Not Started",
            "Owner": {"id": "user-123"},
        }]
    )
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=([], "tok"))

    # Simulate dedup: enqueue_sync returns None (deduplicated)
    mock_enqueue = AsyncMock(return_value=None)
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())

    # Must NOT raise even though enqueue_sync returned None
    await reconcile_sweep(ctx)
    assert mock_enqueue.call_count >= 1


# ---------------------------------------------------------------------------
# Test 10: ZohoAPIError from fetch_tasks_modified_since propagates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_zoho_api_error_surfaces(complete_env, monkeypatch):
    """ZohoAPIError from fetch_tasks_modified_since propagates out of reconcile_sweep."""
    from app.worker.reconciler import reconcile_sweep
    from app.zoho.client import ZohoAPIError

    ctx = _make_reconciler_ctx()
    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(
        side_effect=ZohoAPIError("Zoho API down")
    )
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=([], "tok"))

    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())

    raised = False
    try:
        await reconcile_sweep(ctx)
    except ZohoAPIError as exc:
        raised = True
        assert "Zoho API down" in str(exc)
    assert raised, "Expected ZohoAPIError to propagate but it was swallowed"
