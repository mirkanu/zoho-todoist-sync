"""Unit tests for app.worker.jobs — sync_task pipeline.

Covers: LOOP-1 (echo suppression), LOOP-3 (SELECT FOR UPDATE), LOOP-5 (bootstrap race),
SYNC-11 (LWW conflict resolution), Redis SETNX lock, retry backoff, and write routing.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.core.hash import canonical_hash
from app.core.normalise import NormalisedTask
from app.zoho.client import ZohoAPIError, ZohoRateLimitError, ZohoNotFoundError
from app.todoist.client import TodoistAPIError, TodoistRateLimitError
from arq import Retry


def _norm(title="t", due=None, prio=1, done=False) -> NormalisedTask:
    return NormalisedTask(title=title, due_date=due, priority=prio, is_completed=done)


def _make_ctx(lock_acquired=True, job_try=1):
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True if lock_acquired else None)
    mock_redis.delete = AsyncMock()
    return {
        "redis": mock_redis,
        "session_factory": MagicMock(),   # overridden per-test
        "zoho_client": AsyncMock(),
        "todoist_client": AsyncMock(),
        "job_try": job_try,
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
# Test 1: Lock not acquired → early return, no API call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_task_lock_not_acquired_returns_early(complete_env):
    """When Redis lock is not acquired, return immediately without calling Zoho API."""
    from app.worker.jobs import sync_task  # lazy import: fails until Task 2

    ctx = _make_ctx(lock_acquired=False)
    result = await sync_task(ctx, "Z1")

    assert result is None
    ctx["zoho_client"].get_task.assert_not_called()
    # We never acquired the lock, so we must NOT call delete
    ctx["redis"].delete.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Lock released in finally even when exception raised
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_task_lock_released_in_finally(complete_env):
    """Lock is always released in finally block, even when ZohoAPIError causes Retry."""
    from app.worker.jobs import sync_task

    ctx = _make_ctx(lock_acquired=True, job_try=1)
    ctx["zoho_client"].get_task = AsyncMock(side_effect=ZohoAPIError("boom"))

    factory, sess = _mock_session_factory_with_state(None)
    ctx["session_factory"] = factory

    with pytest.raises(Retry):
        await sync_task(ctx, "Z1")

    ctx["redis"].delete.assert_called_once_with("lock:sync:Z1")


# ---------------------------------------------------------------------------
# Test 3: Echo suppression — both hashes match last_hash (LOOP-1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_echo_suppressed_when_all_hashes_match(complete_env):
    """LOOP-1: When zoho_hash == todoist_hash == last_hash, no write is performed."""
    from app.worker.jobs import sync_task

    norm = _norm(title="task A", prio=2)
    h = canonical_hash(norm)

    state = MagicMock()
    state.todoist_task_id = "T111"
    state.last_hash = h

    factory, sess = _mock_session_factory_with_state(state)
    ctx = _make_ctx()
    ctx["session_factory"] = factory
    ctx["zoho_client"].get_task = AsyncMock(return_value={"title": "task A"})
    ctx["todoist_client"].get_task = AsyncMock(return_value=norm)

    with (
        patch("app.worker.jobs.zoho_record_to_normalised", return_value=norm),
        patch("app.worker.jobs.update_todoist_task") as mock_update_tod,
        patch("app.worker.jobs.update_zoho_task") as mock_update_zoho,
        patch("app.worker.jobs.create_todoist_task") as mock_create,
        patch("app.worker.jobs.complete_todoist_task") as mock_complete_tod,
        patch("app.worker.jobs.complete_zoho_task") as mock_complete_zoho,
    ):
        await sync_task(ctx, "Z1")

    mock_update_tod.assert_not_called()
    mock_update_zoho.assert_not_called()
    mock_create.assert_not_called()
    mock_complete_tod.assert_not_called()
    mock_complete_zoho.assert_not_called()

    # A SyncEvent with action='echo_suppressed' must have been added
    added_events = [
        c.args[0] for c in sess.add.call_args_list
        if hasattr(c.args[0], "action")
    ]
    assert any(e.action == "echo_suppressed" for e in added_events)


# ---------------------------------------------------------------------------
# Test 4: SELECT FOR UPDATE is called (LOOP-3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_select_for_update_is_called(complete_env):
    """LOOP-3: session.execute is called with a select that has with_for_update()."""
    from app.worker.jobs import sync_task

    norm_a = _norm(title="A")
    norm_b = _norm(title="B")
    hash_a = canonical_hash(norm_a)
    hash_b = canonical_hash(norm_b)

    # Existing state with a different hash so we enter the critical section
    state = MagicMock()
    state.todoist_task_id = "T222"
    state.last_hash = "old_hash_value_that_differs_from_both"

    factory, sess = _mock_session_factory_with_state(state)
    ctx = _make_ctx()
    ctx["session_factory"] = factory
    ctx["zoho_client"].get_task = AsyncMock(return_value={})
    ctx["todoist_client"].get_task = AsyncMock(return_value=norm_b)

    with (
        patch("app.worker.jobs.zoho_record_to_normalised", return_value=norm_a),
        patch("app.worker.jobs.update_todoist_task", new_callable=AsyncMock),
        patch("app.worker.jobs.update_zoho_task", new_callable=AsyncMock),
        patch("app.worker.jobs.token_state", {"access_token": "tok"}),
    ):
        await sync_task(ctx, "Z1")

    # Verify that session.execute was called with a statement containing with_for_update
    all_execute_calls = sess.execute.call_args_list
    for c in all_execute_calls:
        stmt = c.args[0] if c.args else None
        if stmt is not None and hasattr(stmt, "_for_update_arg"):
            if stmt._for_update_arg is not None:
                break  # found a FOR UPDATE call
    else:
        # Check via string compilation as fallback
        from sqlalchemy.dialects import postgresql
        for c in all_execute_calls:
            stmt = c.args[0] if c.args else None
            if stmt is not None:
                try:
                    compiled = str(stmt.compile(dialect=postgresql.dialect()))
                    if "FOR UPDATE" in compiled:
                        break
                except Exception:
                    pass
        else:
            pytest.fail("No SELECT FOR UPDATE found in session.execute calls")


# ---------------------------------------------------------------------------
# Test 5: New task — creates in Todoist, writes ID back, inserts sync_state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_task_creates_todoist_and_writes_id_back(complete_env):
    """New task (no sync_state row) → create in Todoist, write ID back, insert sync_state."""
    from app.worker.jobs import sync_task

    norm = _norm(title="New Task")
    factory, sess = _mock_session_factory_with_state(None)
    ctx = _make_ctx()
    ctx["session_factory"] = factory
    ctx["zoho_client"].get_task = AsyncMock(return_value={})

    mock_todoist_api = MagicMock()
    ctx["todoist_client"]._api = mock_todoist_api

    with (
        patch("app.worker.jobs.zoho_record_to_normalised", return_value=norm),
        patch("app.worker.jobs.create_todoist_task", new_callable=AsyncMock, return_value="T999") as mock_create,
        patch("app.worker.jobs.write_todoist_id_to_zoho", new_callable=AsyncMock) as mock_write_back,
        patch("app.worker.jobs.token_state", {"access_token": "tok"}),
    ):
        await sync_task(ctx, "Z1")

    mock_create.assert_called_once_with(norm, "Z1", mock_todoist_api)
    mock_write_back.assert_called_once_with("Z1", "T999", "tok")

    # A SyncState and SyncEvent should have been added to the session
    added = [c.args[0] for c in sess.add.call_args_list]
    from app.db.models import SyncState, SyncEvent
    sync_state_rows = [a for a in added if isinstance(a, SyncState)]
    sync_event_rows = [a for a in added if isinstance(a, SyncEvent)]

    assert len(sync_state_rows) >= 1
    assert sync_state_rows[0].todoist_task_id == "T999"
    assert len(sync_event_rows) >= 1
    assert sync_event_rows[0].action == "sync"
    assert sync_event_rows[0].detail["direction"] == "zoho_to_todoist"


# ---------------------------------------------------------------------------
# Test 6: Bootstrap race — footer suppressed (LOOP-5)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bootstrap_race_footer_suppressed(complete_env):
    """LOOP-5: If Todoist hash matches Zoho hash and last_hash, echo_suppressed path taken."""
    from app.worker.jobs import sync_task

    norm = _norm(title="task", prio=2)
    h = canonical_hash(norm)

    state = MagicMock()
    state.todoist_task_id = "T333"
    state.last_hash = h  # last_hash == zoho_hash == todoist_hash → echo suppressed

    factory, sess = _mock_session_factory_with_state(state)
    ctx = _make_ctx()
    ctx["session_factory"] = factory
    ctx["zoho_client"].get_task = AsyncMock(return_value={})
    ctx["todoist_client"].get_task = AsyncMock(return_value=norm)

    with (
        patch("app.worker.jobs.zoho_record_to_normalised", return_value=norm),
        patch("app.worker.jobs.update_todoist_task") as mock_update_tod,
        patch("app.worker.jobs.update_zoho_task") as mock_update_zoho,
        patch("app.worker.jobs.write_todoist_id_to_zoho") as mock_write_back,
    ):
        await sync_task(ctx, "Z1")

    # No write to either system
    mock_update_tod.assert_not_called()
    mock_update_zoho.assert_not_called()
    mock_write_back.assert_not_called()

    # echo_suppressed logged
    added_events = [c.args[0] for c in sess.add.call_args_list if hasattr(c.args[0], "action")]
    assert any(e.action == "echo_suppressed" for e in added_events)


# ---------------------------------------------------------------------------
# Test 7: LWW — Zoho wins when both sides diverge (SYNC-11)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lww_zoho_wins_when_both_diverge(complete_env):
    """SYNC-11: When both zoho_hash and todoist_hash differ from last_hash, Zoho wins."""
    from app.worker.jobs import sync_task

    norm_zoho = _norm(title="Zoho version")
    norm_todoist = _norm(title="Todoist version")
    zoho_hash = canonical_hash(norm_zoho)
    todoist_hash = canonical_hash(norm_todoist)
    old_hash = "old_hash_" + "x" * 56

    assert zoho_hash != old_hash
    assert todoist_hash != old_hash
    assert zoho_hash != todoist_hash

    state = MagicMock()
    state.todoist_task_id = "T444"
    state.last_hash = old_hash

    factory, sess = _mock_session_factory_with_state(state)
    ctx = _make_ctx()
    ctx["session_factory"] = factory
    ctx["zoho_client"].get_task = AsyncMock(return_value={})
    ctx["todoist_client"].get_task = AsyncMock(return_value=norm_todoist)

    mock_todoist_api = MagicMock()
    ctx["todoist_client"]._api = mock_todoist_api

    with (
        patch("app.worker.jobs.zoho_record_to_normalised", return_value=norm_zoho),
        patch("app.worker.jobs.update_todoist_task", new_callable=AsyncMock) as mock_update_tod,
        patch("app.worker.jobs.update_zoho_task", new_callable=AsyncMock) as mock_update_zoho,
        patch("app.worker.jobs.token_state", {"access_token": "tok"}),
    ):
        await sync_task(ctx, "Z1")

    # Zoho wins → write to Todoist
    mock_update_tod.assert_called_once()
    mock_update_zoho.assert_not_called()

    # state.last_hash set to zoho_hash
    assert state.last_hash == zoho_hash

    # SyncEvent with action='overwrite', direction='zoho_to_todoist'
    added_events = [c.args[0] for c in sess.add.call_args_list if hasattr(c.args[0], "action")]
    overwrite_events = [e for e in added_events if e.action == "overwrite"]
    assert len(overwrite_events) >= 1
    assert overwrite_events[0].detail["direction"] == "zoho_to_todoist"


# ---------------------------------------------------------------------------
# Test 8: Zoho hash differs → write to Todoist
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zoho_hash_differs_writes_to_todoist(complete_env):
    """When zoho_hash != last_hash and todoist_hash == last_hash, write to Todoist."""
    from app.worker.jobs import sync_task

    norm_old = _norm(title="old")
    norm_new = _norm(title="new from zoho")
    old_hash = canonical_hash(norm_old)
    zoho_hash = canonical_hash(norm_new)

    assert zoho_hash != old_hash

    state = MagicMock()
    state.todoist_task_id = "T555"
    state.last_hash = old_hash

    factory, sess = _mock_session_factory_with_state(state)
    ctx = _make_ctx()
    ctx["session_factory"] = factory
    ctx["zoho_client"].get_task = AsyncMock(return_value={})
    ctx["todoist_client"].get_task = AsyncMock(return_value=norm_old)  # same as last_hash

    mock_todoist_api = MagicMock()
    ctx["todoist_client"]._api = mock_todoist_api

    with (
        patch("app.worker.jobs.zoho_record_to_normalised", return_value=norm_new),
        patch("app.worker.jobs.update_todoist_task", new_callable=AsyncMock) as mock_update_tod,
        patch("app.worker.jobs.update_zoho_task", new_callable=AsyncMock) as mock_update_zoho,
        patch("app.worker.jobs.token_state", {"access_token": "tok"}),
    ):
        await sync_task(ctx, "Z1")

    mock_update_tod.assert_called_once()
    mock_update_zoho.assert_not_called()

    added_events = [c.args[0] for c in sess.add.call_args_list if hasattr(c.args[0], "action")]
    sync_events = [e for e in added_events if e.action == "sync"]
    assert len(sync_events) >= 1
    assert sync_events[0].detail["direction"] == "zoho_to_todoist"


# ---------------------------------------------------------------------------
# Test 9: Todoist hash differs → write to Zoho
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_todoist_hash_differs_writes_to_zoho(complete_env):
    """When todoist_hash != last_hash and zoho_hash == last_hash, write to Zoho."""
    from app.worker.jobs import sync_task

    norm_old = _norm(title="old")
    norm_new_todoist = _norm(title="new from todoist")
    old_hash = canonical_hash(norm_old)
    todoist_hash = canonical_hash(norm_new_todoist)

    assert todoist_hash != old_hash

    state = MagicMock()
    state.todoist_task_id = "T666"
    state.last_hash = old_hash

    factory, sess = _mock_session_factory_with_state(state)
    ctx = _make_ctx()
    ctx["session_factory"] = factory
    ctx["zoho_client"].get_task = AsyncMock(return_value={})
    ctx["todoist_client"].get_task = AsyncMock(return_value=norm_new_todoist)

    with (
        patch("app.worker.jobs.zoho_record_to_normalised", return_value=norm_old),  # same as last_hash
        patch("app.worker.jobs.update_todoist_task", new_callable=AsyncMock) as mock_update_tod,
        patch("app.worker.jobs.update_zoho_task", new_callable=AsyncMock) as mock_update_zoho,
        patch("app.worker.jobs.token_state", {"access_token": "tok"}),
    ):
        await sync_task(ctx, "Z1")

    mock_update_zoho.assert_called_once()
    mock_update_tod.assert_not_called()

    added_events = [c.args[0] for c in sess.add.call_args_list if hasattr(c.args[0], "action")]
    sync_events = [e for e in added_events if e.action == "sync"]
    assert len(sync_events) >= 1
    assert sync_events[0].detail["direction"] == "todoist_to_zoho"


# ---------------------------------------------------------------------------
# Test 10: Retry with correct delay for each job_try
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_on_zoho_rate_limit_uses_correct_delay(complete_env):
    """ZohoRateLimitError raises Retry with delay from RETRY_DELAYS[job_try]."""
    from app.worker.jobs import sync_task, RETRY_DELAYS

    for job_try, expected_delay in [(1, 5), (2, 15), (3, 60)]:
        ctx = _make_ctx(lock_acquired=True, job_try=job_try)
        ctx["zoho_client"].get_task = AsyncMock(side_effect=ZohoRateLimitError("rate limit"))

        factory, _ = _mock_session_factory_with_state(None)
        ctx["session_factory"] = factory

        with pytest.raises(Retry) as exc_info:
            await sync_task(ctx, f"Z{job_try}")

        # arq Retry stores defer_score in milliseconds (seconds * 1000)
        actual_delay_secs = exc_info.value.defer_score / 1000
        assert actual_delay_secs == expected_delay, (
            f"job_try={job_try}: expected defer={expected_delay}s, got {actual_delay_secs}s"
        )


# ---------------------------------------------------------------------------
# Test 11: Completion routes to complete_todoist_task, not update_todoist_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completion_routes_to_complete_not_update(complete_env):
    """When is_completed=True and writing to Todoist, complete_todoist_task is called."""
    from app.worker.jobs import sync_task

    norm_old = _norm(title="task", done=False)
    norm_completed = _norm(title="task", done=True)  # marked complete in Zoho
    old_hash = canonical_hash(norm_old)
    zoho_hash = canonical_hash(norm_completed)

    assert zoho_hash != old_hash

    state = MagicMock()
    state.todoist_task_id = "T777"
    state.last_hash = old_hash

    factory, sess = _mock_session_factory_with_state(state)
    ctx = _make_ctx()
    ctx["session_factory"] = factory
    ctx["zoho_client"].get_task = AsyncMock(return_value={})
    ctx["todoist_client"].get_task = AsyncMock(return_value=norm_old)

    mock_todoist_api = MagicMock()
    ctx["todoist_client"]._api = mock_todoist_api

    with (
        patch("app.worker.jobs.zoho_record_to_normalised", return_value=norm_completed),
        patch("app.worker.jobs.complete_todoist_task", new_callable=AsyncMock) as mock_complete,
        patch("app.worker.jobs.update_todoist_task", new_callable=AsyncMock) as mock_update,
        patch("app.worker.jobs.token_state", {"access_token": "tok"}),
    ):
        await sync_task(ctx, "Z1")

    mock_complete.assert_called_once()
    mock_update.assert_not_called()
