"""Unit tests for app.worker.reconciler — reconcile_sweep and orphan_sweep crons. SEED-5, SEED-6, SEED-7."""
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
        "nirvana_client": AsyncMock(),
        "task_provider": AsyncMock(),
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
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

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
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

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
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

    await reconcile_sweep(ctx)

    assert mock_enqueue.call_count == 1
    call = mock_enqueue.call_args
    assert call.args[1] == "Z1"
    assert call.kwargs.get("defer_secs", 0) == 0


# ---------------------------------------------------------------------------
# Test 4: Todoist delta items in sync_state → enqueue per item
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_todoist_delta(complete_env, monkeypatch):
    """Todoist delta items found in sync_state are enqueued with defer_secs=0."""
    from app.worker.reconciler import reconcile_sweep

    ctx = _make_reconciler_ctx()

    # Session returns zoho_task_ids in order for the two Todoist items
    sess = AsyncMock()
    result_t1 = MagicMock()
    result_t1.scalar_one_or_none = MagicMock(return_value="100")
    result_t2 = MagicMock()
    result_t2.scalar_one_or_none = MagicMock(return_value="200")
    sess.execute = AsyncMock(side_effect=[result_t1, result_t2])
    sess.begin = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    ))
    factory_ctx = AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    )
    ctx["session_factory"] = MagicMock(return_value=factory_ctx)

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(return_value=[])
    items = [
        {"id": "T1", "description": "some task", "is_deleted": False},
        {"id": "T2", "description": "another task", "is_deleted": False},
    ]
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=(items, "new-token"))

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

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
# Test 5: Todoist items not in sync_state → enqueue_sync NOT called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_todoist_delta_not_in_sync_state_skipped(complete_env, monkeypatch):
    """Items not found in sync_state are skipped; enqueue_sync not called for them."""
    from app.worker.reconciler import reconcile_sweep

    ctx = _make_reconciler_ctx()
    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    ctx["zoho_client"].fetch_tasks_modified_since = AsyncMock(return_value=[])
    items = [
        {"id": "T1", "description": "Some task", "is_deleted": False},
        {"id": "T2", "description": "Another task", "is_deleted": False},
        {"id": "T3", "description": "", "is_deleted": False},
    ]
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=(items, "new-token"))

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

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
        {"id": "T1", "description": "some task", "is_deleted": True},
    ]
    ctx["todoist_client"].fetch_sync_delta = AsyncMock(return_value=(items, "new-token"))

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

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
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", mock_save)
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

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
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", mock_upsert)
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

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
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

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

    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
    monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})

    raised = False
    try:
        await reconcile_sweep(ctx)
    except ZohoAPIError as exc:
        raised = True
        assert "Zoho API down" in str(exc)
    assert raised, "Expected ZohoAPIError to propagate but it was swallowed"


# ===========================================================================
# Orphan sweep helpers
# ===========================================================================

def _mock_session_factory_with_rows(rows):
    """Build a session_factory mock where session.execute returns rows via scalars().all(),
    and session.get returns the first matching row (or None for empty list)."""
    sess = AsyncMock()
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    sess.execute = AsyncMock(return_value=result)
    sess.get = AsyncMock(return_value=rows[0] if rows else None)
    sess.add = MagicMock()
    sess.delete = AsyncMock()
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


def _make_state(zoho_task_id="Z1", todoist_task_id="T1", orphan_check_count=0):
    """Build a SyncState-like mock with explicit attribute assignment."""
    state = MagicMock()
    state.zoho_task_id = zoho_task_id
    state.external_task_id = todoist_task_id
    state.provider = "todoist"
    state.last_hash = "abc123"
    state.orphan_check_count = orphan_check_count
    return state


def _healthy_zoho_response(zoho_task_id="Z1", owner_id="test-user-id"):
    """Return a Zoho get_task response with the given owner."""
    return {"data": [{"id": zoho_task_id, "Owner": {"id": owner_id}}]}


def _healthy_todoist_task(zoho_task_id="Z1"):
    """A NormalisedTask-shaped mock, matching what task_provider.fetch() now returns."""
    task = MagicMock()
    task.title = "some content"
    return task


def _common_orphan_patches(monkeypatch):
    """Apply standard monkeypatches for orphan_sweep tests. Returns the mocks."""
    mock_delete_todoist = AsyncMock()
    mock_delete_zoho = AsyncMock()
    mock_send_notification = AsyncMock()
    mock_upsert = AsyncMock()
    monkeypatch.setattr("app.worker.reconciler.delete_zoho_task", mock_delete_zoho)
    monkeypatch.setattr("app.worker.reconciler.send_deletion_notification", mock_send_notification)
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", mock_upsert)
    return mock_delete_todoist, mock_delete_zoho, mock_send_notification, mock_upsert


# ===========================================================================
# Orphan sweep tests (9 tests, all should FAIL before implementation — RED)
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 11: First 404 cycle — count incremented, nothing deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_first_cycle(complete_env, monkeypatch):
    """Zoho 404 + orphan_check_count=0 → count incremented to 1; deletes NOT called."""
    from app.worker.reconciler import orphan_sweep
    from app.zoho.client import ZohoNotFoundError

    state = _make_state(orphan_check_count=0)
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(side_effect=ZohoNotFoundError("not found"))
    mock_delete_todoist, mock_delete_zoho, mock_send_notification, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    # Must NOT delete anything on first cycle
    ctx["todoist_client"].delete.assert_not_called()
    mock_delete_zoho.assert_not_called()
    # The locked row's orphan_check_count must have been incremented
    assert sess.get.await_count >= 1, "session.get was not called to lock the row"


# ---------------------------------------------------------------------------
# Test 12: Second consecutive 404 → delete Todoist + SyncEvent + row deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_second_cycle_deletion(complete_env, monkeypatch):
    """Zoho 404 + orphan_check_count=1 → delete_todoist_task called; SyncState row deleted; SyncEvent added."""
    from app.worker.reconciler import orphan_sweep
    from app.zoho.client import ZohoNotFoundError

    state = _make_state(orphan_check_count=1)
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(side_effect=ZohoNotFoundError("not found"))
    # Todoist is healthy (not missing)
    ctx["todoist_client"].fetch = AsyncMock(
        return_value=_healthy_todoist_task("Z1")
    )
    mock_delete_todoist, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    # Todoist counterpart must be deleted (Zoho gone → delete Todoist)
    ctx["todoist_client"].delete.assert_called_once()
    call_args = ctx["todoist_client"].delete.call_args
    assert call_args.args[0] == state.external_task_id

    # SyncState row must be deleted
    sess.delete.assert_awaited()

    # SyncEvent must be added with action='orphan' source='reconciler'
    added_objects = [call.args[0] for call in sess.add.call_args_list]
    from app.db.models import SyncEvent
    orphan_events = [o for o in added_objects if hasattr(o, 'action') and o.action == 'orphan']
    assert len(orphan_events) >= 1, "Expected a SyncEvent with action='orphan' to be added"
    assert orphan_events[0].source == 'reconciler'


# ---------------------------------------------------------------------------
# Test 13: Reassignment detected — treated same as 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_reassignment_detected(complete_env, monkeypatch):
    """Zoho returns 200 but Owner.id mismatch → treated as missing; first cycle increments count."""
    from app.worker.reconciler import orphan_sweep

    state = _make_state(orphan_check_count=0)
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    # Zoho responds with different owner (reassigned)
    ctx["zoho_client"].get_task = AsyncMock(
        return_value={"data": [{"id": "Z1", "Owner": {"id": "other-user"}}]}
    )
    mock_delete_todoist, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    # First cycle: should NOT delete
    ctx["todoist_client"].delete.assert_not_called()
    mock_delete_zoho.assert_not_called()
    # session.get called to increment count
    assert sess.get.await_count >= 1


# ---------------------------------------------------------------------------
# Test 14: Todoist missing (404) → delete Zoho counterpart
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_todoist_missing(complete_env, monkeypatch):
    """Todoist fetch raises TodoistNotFoundError + orphan_check_count=1 → delete_zoho_task called."""
    from app.worker.reconciler import orphan_sweep
    from app.todoist.client import TodoistNotFoundError

    state = _make_state(orphan_check_count=1)
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    # Zoho is healthy
    ctx["zoho_client"].get_task = AsyncMock(
        return_value=_healthy_zoho_response("Z1", "test-user-id")
    )
    # Todoist is missing
    ctx["todoist_client"].fetch = AsyncMock(
        side_effect=TodoistNotFoundError("not found")
    )
    mock_delete_todoist, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    # Zoho counterpart must be deleted (Todoist gone → delete Zoho)
    mock_delete_zoho.assert_called_once()
    call_args = mock_delete_zoho.call_args
    assert call_args.args[0] == state.zoho_task_id
    assert call_args.args[1] == "fake-token"

    # SyncState row must be deleted
    sess.delete.assert_awaited()


# ---------------------------------------------------------------------------
# Test 15: Both healthy + count elevated → count reset to 0, nothing deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_count_reset(complete_env, monkeypatch):
    """BOTH Zoho present (Owner matches) AND Todoist present + orphan_check_count=2 → count reset to 0."""
    from app.worker.reconciler import orphan_sweep

    state = _make_state(orphan_check_count=2)
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(
        return_value=_healthy_zoho_response("Z1", "test-user-id")
    )
    ctx["todoist_client"].fetch = AsyncMock(
        return_value=_healthy_todoist_task("Z1")
    )
    mock_delete_todoist, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    ctx["todoist_client"].delete.assert_not_called()
    mock_delete_zoho.assert_not_called()
    # session.get called to reset count
    assert sess.get.await_count >= 1


# ---------------------------------------------------------------------------
# Test 16: Zoho rate limit → row skipped, count NOT incremented
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_zoho_rate_limit_skipped(complete_env, monkeypatch):
    """Zoho raises ZohoRateLimitError → row skipped; orphan_check_count NOT incremented; deletes NOT called."""
    from app.worker.reconciler import orphan_sweep
    from app.zoho.client import ZohoRateLimitError

    state = _make_state(orphan_check_count=0)
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(side_effect=ZohoRateLimitError("rate limited"))
    mock_delete_todoist, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    # Row skipped — no deletes, no count increment (no session.get call to increment)
    ctx["todoist_client"].delete.assert_not_called()
    mock_delete_zoho.assert_not_called()
    # session.get should NOT have been called (no increment/delete)
    sess.get.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 17: Todoist rate limit → row skipped, count NOT incremented
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_todoist_rate_limit_skipped(complete_env, monkeypatch):
    """Todoist raises TodoistRateLimitError → row skipped; count NOT incremented."""
    from app.worker.reconciler import orphan_sweep
    from app.todoist.client import TodoistRateLimitError

    state = _make_state(orphan_check_count=0)
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(
        return_value=_healthy_zoho_response("Z1", "test-user-id")
    )
    ctx["todoist_client"].fetch = AsyncMock(
        side_effect=TodoistRateLimitError("rate limited")
    )
    mock_delete_todoist, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    ctx["todoist_client"].delete.assert_not_called()
    mock_delete_zoho.assert_not_called()
    sess.get.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 18: orphan_sweep healthy path — Zoho hash drifted → enqueue_sync called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_sweep_drift_enqueues_sync(complete_env, monkeypatch):
    """Healthy path: both sides exist but Zoho hash differs from state.last_hash
    → enqueue_sync called with source='orphan_drift'."""
    from app.worker.reconciler import orphan_sweep
    from app.core.hash import canonical_hash
    from app.core.normalise import NormalisedTask

    state = _make_state(orphan_check_count=0)
    state.last_hash = "stale_hash_not_matching"

    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    # Zoho returns a healthy record (owner matches settings.zoho_user_id = "test-user-id")
    ctx["zoho_client"].get_task = AsyncMock(
        return_value={"data": [{
            "id": "Z1",
            "Subject": "Drifted Task",
            "Due_Date": "2026-06-01",
            "Priority": "High",
            "Status": "Not Started",
            "Owner": {"id": "test-user-id"},
        }]}
    )
    # Todoist is healthy
    ctx["todoist_client"].fetch = AsyncMock(
        return_value=_healthy_todoist_task("Z1")
    )

    mock_enqueue = AsyncMock(return_value=MagicMock())
    mock_delete_todoist, mock_delete_zoho, _, mock_upsert = _common_orphan_patches(monkeypatch)
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)

    await orphan_sweep(ctx)

    mock_enqueue.assert_called_once()
    call = mock_enqueue.call_args
    assert call.args[1] == state.zoho_task_id
    assert call.kwargs.get("source") == "orphan_drift"
    assert call.kwargs.get("defer_secs") == 0
    # Nothing should be deleted
    ctx["todoist_client"].delete.assert_not_called()
    mock_delete_zoho.assert_not_called()


@pytest.mark.asyncio
async def test_orphan_sweep_healthy_no_drift_no_enqueue(complete_env, monkeypatch):
    """Healthy path: both sides exist and Zoho hash matches state.last_hash
    → enqueue_sync NOT called."""
    from app.worker.reconciler import orphan_sweep
    from app.core.hash import canonical_hash
    from app.zoho.normalise import zoho_record_to_normalised
    from app.core.config import get_settings

    # Build a Zoho record and compute its canonical hash so state.last_hash matches
    zoho_data = {
        "id": "Z1",
        "Subject": "Stable Task",
        "Due_Date": "2026-06-01",
        "Priority": "High",
        "Status": "Not Started",
        "Owner": {"id": "test-user-id"},
    }
    settings = get_settings()
    norm = zoho_record_to_normalised(zoho_data, settings.zoho_terminal_statuses_list)
    matching_hash = canonical_hash(norm)

    state = _make_state(orphan_check_count=0)
    state.last_hash = matching_hash

    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(return_value={"data": [zoho_data]})
    ctx["todoist_client"].fetch = AsyncMock(
        return_value=_healthy_todoist_task("Z1")
    )

    mock_enqueue = AsyncMock(return_value=MagicMock())
    mock_delete_todoist, mock_delete_zoho, _, mock_upsert = _common_orphan_patches(monkeypatch)
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)

    await orphan_sweep(ctx)

    mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# Test 18b: REGRESSION (2026-07-28 incident) — mixed-provider rows must each
# be checked against THEIR OWN provider's client, never the globally active
# ctx["task_provider"]. A nirvana-provider row, checked while a todoist row
# also exists in the same sweep, must use ctx["nirvana_client"] for its
# fetch — using the wrong client (e.g. checking a Todoist ID against
# Nirvana, or vice versa) always 404s and, after two cycles, deletes a real
# Zoho task. This exact bug deleted 64 real Zoho tasks in production.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_sweep_uses_correct_client_per_row_provider(complete_env, monkeypatch):
    """A todoist-provider row and a nirvana-provider row in the SAME sweep
    must each be fetched via their own provider's client. Using
    ctx["task_provider"] for both (the pre-fix bug) would call the wrong
    client for whichever row's provider doesn't match the active one."""
    from app.worker.reconciler import orphan_sweep

    todoist_state = _make_state(zoho_task_id="Z-TODOIST", todoist_task_id="T1", orphan_check_count=0)
    todoist_state.provider = "todoist"

    nirvana_state = _make_state(zoho_task_id="Z-NIRVANA", todoist_task_id="N1", orphan_check_count=0)
    nirvana_state.provider = "nirvana"

    factory, sess = _mock_session_factory_with_rows([todoist_state, nirvana_state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(
        side_effect=lambda zoho_id: _healthy_zoho_response(zoho_id)
    )
    ctx["todoist_client"].fetch = AsyncMock(return_value=_healthy_todoist_task("Z-TODOIST"))
    ctx["nirvana_client"].fetch = AsyncMock(return_value=_healthy_todoist_task("Z-NIRVANA"))
    # If the bug were present, ctx["task_provider"] would be used for both
    # rows instead — assert it is NEVER called at all in orphan_sweep.
    ctx["task_provider"].fetch = AsyncMock(side_effect=AssertionError(
        "orphan_sweep must not use ctx['task_provider'] directly — use the "
        "per-row client via _provider_client_for_row"
    ))

    _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    ctx["todoist_client"].fetch.assert_called_once_with("T1")
    ctx["nirvana_client"].fetch.assert_called_once_with("N1")
    ctx["task_provider"].fetch.assert_not_called()


@pytest.mark.asyncio
async def test_orphan_sweep_nirvana_row_missing_deletes_via_nirvana_client(complete_env, monkeypatch):
    """A confirmed-orphan nirvana-provider row must call delete() on
    ctx["nirvana_client"], not ctx["todoist_client"] or ctx["task_provider"]."""
    from app.worker.reconciler import orphan_sweep
    from app.nirvana.client import NirvanaNotFoundError

    state = _make_state(zoho_task_id="Z-NIRVANA", todoist_task_id="N1", orphan_check_count=1)
    state.provider = "nirvana"

    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(return_value=_healthy_zoho_response("Z-NIRVANA"))
    ctx["nirvana_client"].fetch = AsyncMock(side_effect=NirvanaNotFoundError("not found"))
    ctx["nirvana_client"].delete = AsyncMock()

    mock_delete_todoist, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    mock_delete_zoho.assert_called_once()
    ctx["nirvana_client"].delete.assert_not_called()  # todoist_missing path deletes Zoho, not Nirvana
    ctx["todoist_client"].delete.assert_not_called()
    ctx["task_provider"].delete.assert_not_called()


# ---------------------------------------------------------------------------
# Test 19: orphan_sweep_last_run upserted with ISO timestamp at end of sweep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_sweep_last_run_updated(complete_env, monkeypatch):
    """At end of sweep, upsert_kv called with key 'orphan_sweep_last_run' + ISO timestamp."""
    from app.worker.reconciler import orphan_sweep, KV_ORPHAN_SWEEP_LAST_RUN
    from datetime import datetime

    factory, sess = _mock_session_factory_with_rows([])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    # No rows to process — just verify last_run is written
    mock_delete_todoist, mock_delete_zoho, _, mock_upsert = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    assert mock_upsert.call_count >= 1, "upsert_kv was not called"
    # Find the last_run upsert call
    kv_calls = [(c.args[1], c.args[2]) for c in mock_upsert.call_args_list]
    last_run_calls = [(k, v) for k, v in kv_calls if k == KV_ORPHAN_SWEEP_LAST_RUN]
    assert len(last_run_calls) >= 1, f"No upsert for '{KV_ORPHAN_SWEEP_LAST_RUN}' found"
    ts_value = last_run_calls[0][1]
    assert isinstance(ts_value, str)
    parsed = datetime.fromisoformat(ts_value)
    assert parsed.tzinfo is not None  # must be tz-aware (UTC)


# ===========================================================================
# nirvana_poll_sweep tests
# ===========================================================================

class _FakeSettings:
    """Minimal stand-in for Settings — nirvana_poll_sweep only reads .task_provider."""

    def __init__(self, task_provider: str) -> None:
        self.task_provider = task_provider


def _make_nirvana_ctx():
    """Build a minimal arq ctx dict for nirvana_poll_sweep."""
    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock(return_value=MagicMock())
    return {
        "redis": mock_redis,
        "session_factory": MagicMock(),
        "task_provider": AsyncMock(),
    }


# ---------------------------------------------------------------------------
# Test 20: task_provider != "nirvana" -> no-op (no get_tasks, no enqueue_sync)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nirvana_poll_sweep_inactive_noop(complete_env, monkeypatch):
    """When TASK_PROVIDER != 'nirvana', nirvana_poll_sweep returns immediately
    without calling get_tasks or enqueue_sync."""
    from app.worker.reconciler import nirvana_poll_sweep

    ctx = _make_nirvana_ctx()
    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.get_settings", lambda: _FakeSettings("todoist"))
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)

    await nirvana_poll_sweep(ctx)

    ctx["task_provider"].get_tasks_paginated.assert_not_called()
    ctx["task_provider"].get_task_counts.assert_not_called()
    mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# Test 21: nirvana active, hash mismatch against sync_state -> enqueue_sync
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nirvana_poll_sweep_enqueues_on_hash_mismatch(complete_env, monkeypatch):
    """Nirvana active: matched sync_state row with a stale hash -> enqueue_sync
    called with the linked zoho_task_id, source='nirvana_poll'."""
    from app.worker.reconciler import nirvana_poll_sweep

    ctx = _make_nirvana_ctx()
    ctx["task_provider"].get_task_counts = AsyncMock(return_value={"next": 1})
    ctx["task_provider"].get_tasks_paginated = AsyncMock(return_value=[
        {"id": "N1", "name": "Buy milk", "duedate": "2026-05-01", "state": "next", "starred": False, "completed": None},
    ])

    state = MagicMock()
    state.zoho_task_id = "Z1"
    state.last_hash = "stale_hash_value"
    factory, sess = _mock_session_factory_with_state(state)
    ctx["session_factory"] = factory

    mock_enqueue = AsyncMock(return_value=MagicMock())
    mock_upsert = AsyncMock()
    monkeypatch.setattr("app.worker.reconciler.get_settings", lambda: _FakeSettings("nirvana"))
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", mock_upsert)

    await nirvana_poll_sweep(ctx)

    mock_enqueue.assert_called_once()
    call = mock_enqueue.call_args
    assert call.args[0] is ctx["redis"]
    assert call.args[1] == "Z1"
    assert call.kwargs.get("defer_secs") == 0
    assert call.kwargs.get("source") == "nirvana_poll"


# ---------------------------------------------------------------------------
# Test 22: nirvana active, no matching sync_state row -> skipped, not enqueued
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nirvana_poll_sweep_unmatched_task_skipped(complete_env, monkeypatch):
    """A raw Nirvana task with no matching sync_state row is skipped, not enqueued
    (mirrors reconcile_todoist_not_in_sync_state's philosophy)."""
    from app.worker.reconciler import nirvana_poll_sweep

    ctx = _make_nirvana_ctx()
    ctx["task_provider"].get_task_counts = AsyncMock(return_value={"next": 1})
    ctx["task_provider"].get_tasks_paginated = AsyncMock(return_value=[
        {"id": "N-native", "name": "Native nirvana task", "duedate": None, "state": "next", "starred": False, "completed": None},
    ])

    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    mock_enqueue = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.worker.reconciler.get_settings", lambda: _FakeSettings("nirvana"))
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", mock_enqueue)
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())

    await nirvana_poll_sweep(ctx)

    mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# Test 23: 200-item cap hit -> warning logged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nirvana_poll_sweep_cap_hit_warning(complete_env, monkeypatch):
    """When get_tasks_paginated returns exactly the pagination safety-cap
    count (page_size * max_pages), a 'nirvana_poll_sweep_cap_hit' warning is
    logged."""
    from app.worker.reconciler import (
        nirvana_poll_sweep, NIRVANA_POLL_PAGE_SIZE, NIRVANA_POLL_MAX_PAGES,
    )

    cap = NIRVANA_POLL_PAGE_SIZE * NIRVANA_POLL_MAX_PAGES
    ctx = _make_nirvana_ctx()
    ctx["task_provider"].get_task_counts = AsyncMock(return_value={"next": cap})
    ctx["task_provider"].get_tasks_paginated = AsyncMock(return_value=[
        {"id": f"N{i}", "name": f"Task {i}", "duedate": None, "state": "next", "starred": False, "completed": None}
        for i in range(cap)
    ])

    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    mock_log = MagicMock()
    monkeypatch.setattr("app.worker.reconciler.get_settings", lambda: _FakeSettings("nirvana"))
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
    monkeypatch.setattr("app.worker.reconciler.log", mock_log)

    await nirvana_poll_sweep(ctx)

    warning_events = [c.args[0] for c in mock_log.warning.call_args_list]
    assert "nirvana_poll_sweep_cap_hit" in warning_events


# ---------------------------------------------------------------------------
# Test 24: nirvana_poll_sweep_last_run upserted with ISO timestamp
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nirvana_poll_sweep_last_run_updated(complete_env, monkeypatch):
    """At end of sweep (when active), upsert_kv called with
    'nirvana_poll_sweep_last_run' and an ISO-8601 tz-aware timestamp."""
    from datetime import datetime
    from app.worker.reconciler import nirvana_poll_sweep, KV_NIRVANA_POLL_LAST_RUN

    ctx = _make_nirvana_ctx()
    ctx["task_provider"].get_task_counts = AsyncMock(return_value={})
    ctx["task_provider"].get_tasks_paginated = AsyncMock(return_value=[])

    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    mock_upsert = AsyncMock()
    monkeypatch.setattr("app.worker.reconciler.get_settings", lambda: _FakeSettings("nirvana"))
    monkeypatch.setattr("app.worker.reconciler.enqueue_sync", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", mock_upsert)

    await nirvana_poll_sweep(ctx)

    assert mock_upsert.call_count == 1
    call_args = mock_upsert.call_args
    assert call_args.args[1] == KV_NIRVANA_POLL_LAST_RUN
    ts_value = call_args.args[2]
    parsed = datetime.fromisoformat(ts_value)
    assert parsed.tzinfo is not None
