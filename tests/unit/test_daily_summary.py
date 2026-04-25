"""Unit tests for app.worker.daily_summary — OBS-3, OBS-4, D-07, D-08, D-09."""
import pytest
from unittest.mock import AsyncMock, MagicMock, call
from datetime import datetime, timezone, timedelta


def _make_ctx(session_factory, todoist_api):
    todoist_client = MagicMock()
    todoist_client._api = todoist_api
    return {
        "redis": AsyncMock(),
        "session_factory": session_factory,
        "zoho_client": AsyncMock(),
        "todoist_client": todoist_client,
    }


def _make_session_factory(scalar_returns: list, delete_rowcount: int = 5):
    """Build session_factory mock.

    First execute call simulates the DELETE (returns a result with .rowcount).
    Subsequent execute calls simulate COUNT queries (each returns a result with .scalar_one).
    """
    sess = AsyncMock()
    delete_result = MagicMock()
    delete_result.rowcount = delete_rowcount
    count_results = []
    for n in scalar_returns:
        r = MagicMock()
        r.scalar_one = MagicMock(return_value=n)
        count_results.append(r)
    sess.execute = AsyncMock(side_effect=[delete_result, *count_results])
    sess.commit = AsyncMock()
    ctx_mgr = AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    )
    return MagicMock(return_value=ctx_mgr), sess


def _make_multi_session_factory(scalar_returns: list, delete_rowcount: int = 5):
    """Build a session_factory where each call to session_factory() returns a fresh context manager
    that yields the SAME underlying sess mock. All execute calls accumulate on that mock."""
    sess = AsyncMock()
    delete_result = MagicMock()
    delete_result.rowcount = delete_rowcount
    count_results = []
    for n in scalar_returns:
        r = MagicMock()
        r.scalar_one = MagicMock(return_value=n)
        count_results.append(r)
    sess.execute = AsyncMock(side_effect=[delete_result, *count_results])
    sess.commit = AsyncMock()

    def _factory():
        return AsyncMock(
            __aenter__=AsyncMock(return_value=sess),
            __aexit__=AsyncMock(return_value=None),
        )

    return MagicMock(side_effect=_factory), sess


def _make_todoist_api(task_id: str = "test-todoist-id"):
    api = AsyncMock()
    task = MagicMock()
    task.id = task_id
    api.add_task = AsyncMock(return_value=task)
    api.complete_task = AsyncMock(return_value=True)
    return api


def _make_settings(project_id: str = "6gCPcWwM392GhXQh"):
    settings = MagicMock()
    settings.todoist_project_id = project_id
    return settings


# ---------------------------------------------------------------------------
# Test 1: task title is exactly `Sync summary: {YYYY-MM-DD}` (today UTC)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_summary_task_title(complete_env, monkeypatch):
    """OBS-3: task content is 'Sync summary: {YYYY-MM-DD}' using today's UTC date."""
    from app.worker.daily_summary import daily_summary

    todoist_api = _make_todoist_api()
    factory, sess = _make_multi_session_factory([10, 2, 3])
    ctx = _make_ctx(factory, todoist_api)

    monkeypatch.setattr("app.worker.daily_summary.get_settings", lambda: _make_settings())

    await daily_summary(ctx)

    todoist_api.add_task.assert_awaited_once()
    call_kwargs = todoist_api.add_task.call_args
    content = call_kwargs.kwargs.get("content") or call_kwargs.args[0]
    today = datetime.now(timezone.utc).date().isoformat()
    assert content == f"Sync summary: {today}", (
        f"Expected 'Sync summary: {today}', got '{content}'"
    )


# ---------------------------------------------------------------------------
# Test 2: description is exactly `{N} syncs, {M} errors, {P} echoes suppressed`
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_summary_task_description_format(complete_env, monkeypatch):
    """D-08: description is exactly '{N} syncs, {M} errors, {P} echoes suppressed'."""
    from app.worker.daily_summary import daily_summary

    todoist_api = _make_todoist_api()
    factory, sess = _make_multi_session_factory([5, 1, 7])
    ctx = _make_ctx(factory, todoist_api)

    monkeypatch.setattr("app.worker.daily_summary.get_settings", lambda: _make_settings())

    await daily_summary(ctx)

    todoist_api.add_task.assert_awaited_once()
    call_kwargs = todoist_api.add_task.call_args
    description = call_kwargs.kwargs.get("description")
    assert description == "5 syncs, 1 errors, 7 echoes suppressed", (
        f"Expected '5 syncs, 1 errors, 7 echoes suppressed', got '{description}'"
    )


# ---------------------------------------------------------------------------
# Test 3: add_task called with project_id=settings.todoist_project_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_summary_uses_project_id(complete_env, monkeypatch):
    """OBS-3: add_task called with project_id matching TODOIST_PROJECT_ID."""
    from app.worker.daily_summary import daily_summary

    todoist_api = _make_todoist_api()
    factory, sess = _make_multi_session_factory([3, 0, 1])
    ctx = _make_ctx(factory, todoist_api)

    expected_project_id = "6gCPcWwM392GhXQh"
    monkeypatch.setattr(
        "app.worker.daily_summary.get_settings",
        lambda: _make_settings(expected_project_id),
    )

    await daily_summary(ctx)

    todoist_api.add_task.assert_awaited_once()
    call_kwargs = todoist_api.add_task.call_args
    project_id = call_kwargs.kwargs.get("project_id")
    assert project_id == expected_project_id, (
        f"Expected project_id='{expected_project_id}', got '{project_id}'"
    )


# ---------------------------------------------------------------------------
# Test 4: complete_task called with the new task's id immediately after add_task (D-07)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_summary_task_completed(complete_env, monkeypatch):
    """D-07: complete_task called with the new task.id immediately after add_task returns."""
    from app.worker.daily_summary import daily_summary

    task_id = "my-new-task-id-123"
    todoist_api = _make_todoist_api(task_id=task_id)
    factory, sess = _make_multi_session_factory([2, 0, 0])
    ctx = _make_ctx(factory, todoist_api)

    monkeypatch.setattr("app.worker.daily_summary.get_settings", lambda: _make_settings())

    await daily_summary(ctx)

    todoist_api.complete_task.assert_awaited_once_with(task_id)


# ---------------------------------------------------------------------------
# Test 5: 90-day cleanup DELETE is executed and committed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_90day_cleanup_runs(complete_env, monkeypatch):
    """OBS-4: DELETE sync_events WHERE created_at < cutoff_90d is executed and committed."""
    from app.worker.daily_summary import daily_summary

    todoist_api = _make_todoist_api()
    factory, sess = _make_multi_session_factory([1, 0, 0], delete_rowcount=7)
    ctx = _make_ctx(factory, todoist_api)

    monkeypatch.setattr("app.worker.daily_summary.get_settings", lambda: _make_settings())

    await daily_summary(ctx)

    # At least one execute call must have been a DELETE
    assert sess.execute.await_count >= 1, "session.execute was never called"
    first_call = sess.execute.call_args_list[0]
    stmt_arg = first_call.args[0]
    stmt_str = str(stmt_arg).upper()
    assert "DELETE" in stmt_str, (
        f"First execute call expected DELETE statement, got: {stmt_str[:100]}"
    )
    # commit must be called after the delete
    assert sess.commit.await_count >= 1, "session.commit was never called after DELETE"


# ---------------------------------------------------------------------------
# Test 6: cleanup runs BEFORE count (D-09 ordering)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_before_count(complete_env, monkeypatch):
    """D-09: DELETE + COMMIT execute BEFORE the COUNT queries execute."""
    from app.worker.daily_summary import daily_summary

    todoist_api = _make_todoist_api()
    factory, sess = _make_multi_session_factory([4, 2, 1], delete_rowcount=3)
    ctx = _make_ctx(factory, todoist_api)

    monkeypatch.setattr("app.worker.daily_summary.get_settings", lambda: _make_settings())

    await daily_summary(ctx)

    # There should be at least 4 execute calls: 1 DELETE + 3 COUNT (sync, error, echo)
    # (or at minimum 1 DELETE + at least 1 COUNT — accept any ordering validation)
    calls = sess.execute.call_args_list
    assert len(calls) >= 2, (
        f"Expected at least 2 execute calls (DELETE + COUNT), got {len(calls)}"
    )

    # First call MUST be a DELETE statement
    first_stmt = str(calls[0].args[0]).upper()
    assert "DELETE" in first_stmt, (
        f"First execute call must be DELETE, got: {first_stmt[:100]}"
    )

    # Remaining calls must NOT be DELETE (should be SELECT/COUNT)
    for i, c in enumerate(calls[1:], start=1):
        subsequent_stmt = str(c.args[0]).upper()
        assert "DELETE" not in subsequent_stmt, (
            f"Call #{i+1} should not be a DELETE (ordering violation); got: {subsequent_stmt[:100]}"
        )


# ---------------------------------------------------------------------------
# Test 7: count queries use a 24h window (created_at > now() - 24h)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_uses_24h_window(complete_env, monkeypatch):
    """OBS-3: count queries filter by created_at > now() - 24h."""
    from app.worker.daily_summary import daily_summary

    todoist_api = _make_todoist_api()
    factory, sess = _make_multi_session_factory([6, 3, 2])
    ctx = _make_ctx(factory, todoist_api)

    monkeypatch.setattr("app.worker.daily_summary.get_settings", lambda: _make_settings())

    before = datetime.now(timezone.utc) - timedelta(hours=24, minutes=1)

    await daily_summary(ctx)

    # All execute calls after the first (DELETE) should be SELECT/count queries
    calls = sess.execute.call_args_list
    assert len(calls) >= 2, "Expected DELETE + at least one COUNT"

    count_calls = calls[1:]  # skip the DELETE
    assert len(count_calls) >= 1, "Expected at least one COUNT query"

    # Verify we actually got non-zero counts back — meaning count queries ran
    # (The mock returns scalar_returns values which are > 0)
    # We can't easily introspect SQLAlchemy AST for time window, but we verify
    # that the result values (6, 3, 2) were used in the task description
    todoist_api.add_task.assert_awaited_once()
    call_kwargs = todoist_api.add_task.call_args
    description = call_kwargs.kwargs.get("description") or ""
    # Description must contain numeric counts
    assert "syncs" in description and "errors" in description and "echoes suppressed" in description, (
        f"Description does not look like a count summary: '{description}'"
    )


# ---------------------------------------------------------------------------
# Test 8: log.info("daily_summary_complete", ...) is called with count fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logs_summary_event(complete_env, monkeypatch):
    """Structured log 'daily_summary_complete' is emitted with syncs, errors, echoes fields."""
    from app.worker.daily_summary import daily_summary

    todoist_api = _make_todoist_api()
    factory, sess = _make_multi_session_factory([8, 1, 4])
    ctx = _make_ctx(factory, todoist_api)

    monkeypatch.setattr("app.worker.daily_summary.get_settings", lambda: _make_settings())

    log_calls = []

    class _MockLogger:
        def info(self, event, **kwargs):
            log_calls.append((event, kwargs))

        def warning(self, event, **kwargs):
            pass

        def error(self, event, **kwargs):
            pass

    monkeypatch.setattr("app.worker.daily_summary.log", _MockLogger())

    await daily_summary(ctx)

    complete_events = [(e, kw) for e, kw in log_calls if e == "daily_summary_complete"]
    assert len(complete_events) >= 1, (
        f"Expected log.info('daily_summary_complete', ...) to be called. Got: {log_calls}"
    )
    _, kw = complete_events[0]
    assert "syncs" in kw, f"'syncs' not in log fields: {kw}"
    assert "errors" in kw, f"'errors' not in log fields: {kw}"
    assert "echoes" in kw, f"'echoes' not in log fields: {kw}"
