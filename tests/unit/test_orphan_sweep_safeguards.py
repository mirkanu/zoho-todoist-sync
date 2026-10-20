"""Unit tests for the 2026-10-20 orphan_sweep safeguards:
- ZTS_DISABLE_ORPHAN_SWEEP kill-switch
- Longer confirm cycle for mixed-provider rows
- First-cycle threshold alert

See memory `2026-10-20-orphan-sweep-incident`.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers duplicated from test_reconciler.py so this file stays self-contained
# ---------------------------------------------------------------------------

def _make_reconciler_ctx():
    """Minimal arq ctx dict for orphan_sweep."""
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


def _mock_session_factory_with_rows(rows):
    """session_factory mock whose session.execute returns rows via
    scalars().all() and whose session.get returns the first row (or None)."""
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


def _make_state(zoho_task_id="Z1", todoist_task_id="T1", orphan_check_count=0, provider="todoist"):
    state = MagicMock()
    state.zoho_task_id = zoho_task_id
    state.external_task_id = todoist_task_id
    state.provider = provider
    state.last_hash = "abc123"
    state.orphan_check_count = orphan_check_count
    return state


def _healthy_zoho_response(zoho_task_id="Z1", owner_id="test-user-id"):
    return {"data": [{"id": zoho_task_id, "Owner": {"id": owner_id}}]}


def _healthy_todoist_task():
    task = MagicMock()
    task.title = "some content"
    return task


def _common_orphan_patches(monkeypatch):
    mock_delete_todoist = AsyncMock()
    mock_delete_zoho = AsyncMock()
    mock_send_notification = AsyncMock()
    mock_upsert = AsyncMock()
    monkeypatch.setattr("app.worker.reconciler.delete_zoho_task", mock_delete_zoho)
    monkeypatch.setattr("app.worker.reconciler.send_deletion_notification", mock_send_notification)
    monkeypatch.setattr("app.worker.reconciler.token_state", {"access_token": "fake-token"})
    monkeypatch.setattr("app.worker.reconciler.upsert_kv", mock_upsert)
    return mock_delete_todoist, mock_delete_zoho, mock_send_notification, mock_upsert


@pytest.fixture
def fresh_settings(complete_env, monkeypatch):
    """Force Settings() to be rebuilt after per-test monkeypatch.setenv calls
    (Settings is lru_cached on construction; env tweaks don't propagate
    otherwise). Depends on complete_env so the env is set BEFORE the lazy
    `from app.core import config` inside this fixture triggers Settings()."""
    from app.core import config as cfg
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


# ===========================================================================
# Kill-switch tests
# ===========================================================================

@pytest.mark.asyncio
async def test_orphan_sweep_kill_switch_short_circuits(fresh_settings, monkeypatch):
    """ZTS_DISABLE_ORPHAN_SWEEP=1 short-circuits orphan_sweep — no Zoho or
    external-client fetches, no deletes, no DB mutations beyond the
    monitoring timestamp."""
    from app.worker.reconciler import orphan_sweep

    monkeypatch.setenv("ZTS_DISABLE_ORPHAN_SWEEP", "1")

    state = _make_state(orphan_check_count=1)  # would trigger deletion if reach logic
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    mock_delete_todoist, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    # Sweep never touched Zoho, never touched external clients.
    ctx["zoho_client"].get_task.assert_not_called()
    ctx["todoist_client"].fetch.assert_not_called()
    ctx["nirvana_client"].fetch.assert_not_called()
    # No deletes.
    mock_delete_todoist.assert_not_called()
    mock_delete_zoho.assert_not_called()
    # No increment/delete at the DB layer (begin() should not have been called).
    sess.begin.assert_not_called()
    # No SyncState row deletion.
    sess.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_orphan_sweep_kill_switch_persists_last_run(fresh_settings, monkeypatch):
    """The kill-switch branch still writes orphan_sweep_last_run so
    monitoring sees the sweep ran (a missing timestamp == visible alarm)."""
    from app.worker.reconciler import orphan_sweep, KV_ORPHAN_SWEEP_LAST_RUN

    monkeypatch.setenv("ZTS_DISABLE_ORPHAN_SWEEP", "1")

    factory, sess = _mock_session_factory_with_rows([])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    _, _, _, mock_upsert = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    kv_calls = [(c.args[1], c.args[2]) for c in mock_upsert.call_args_list]
    last_run_calls = [(k, v) for k, v in kv_calls if k == KV_ORPHAN_SWEEP_LAST_RUN]
    assert len(last_run_calls) == 1, f"Expected exactly one last-run upsert, got {kv_calls}"
    ts_value = last_run_calls[0][1]
    assert isinstance(ts_value, str)
    parsed = datetime.fromisoformat(ts_value)
    assert parsed.tzinfo is not None


# ===========================================================================
# Mixed-provider cycle tests
# ===========================================================================

@pytest.mark.asyncio
async def test_orphan_sweep_mixed_provider_three_cycles_to_delete(fresh_settings, monkeypatch):
    """A row whose provider != active provider requires 3 consecutive
    missing cycles before deletion, not the default 2. With count==2
    the sweep MUST delete; with count==1 it MUST NOT delete."""
    from app.worker.reconciler import orphan_sweep
    from app.todoist.client import TodoistNotFoundError

    monkeypatch.setenv("TASK_PROVIDER", "todoist")  # active = todoist
    monkeypatch.setenv("ZTS_ORPHAN_MIXED_PROVIDER_CONFIRM_CYCLES", "3")

    # Row whose own provider is nirvana (mixed vs active todoist).
    state = _make_state(
        zoho_task_id="Z-MIX", todoist_task_id="N-MIX", orphan_check_count=2, provider="nirvana",
    )
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    # Third consecutive missing detection: nirvana missing, zoho healthy.
    ctx["zoho_client"].get_task = AsyncMock(return_value=_healthy_zoho_response("Z-MIX"))
    ctx["nirvana_client"].fetch = AsyncMock(side_effect=TodoistNotFoundError("not found"))
    ctx["nirvana_client"].delete = AsyncMock()
    _, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    # count==2 reached; the third observation triggers deletion. todoist_missing
    # path deletes the Zoho counterpart (uses nirvana_client is impossible
    # here because we set zoho as healthy and the external fetch as missing).
    mock_delete_zoho.assert_called_once()
    sess.delete.assert_awaited()


@pytest.mark.asyncio
async def test_orphan_sweep_mixed_provider_count_one_does_not_delete(fresh_settings, monkeypatch):
    """First-cycle row with mixed provider: count==1, only second of three
    observed. MUST NOT delete, MUST increment."""
    from app.worker.reconciler import orphan_sweep
    from app.todoist.client import TodoistNotFoundError

    monkeypatch.setenv("TASK_PROVIDER", "todoist")
    monkeypatch.setenv("ZTS_ORPHAN_MIXED_PROVIDER_CONFIRM_CYCLES", "3")

    state = _make_state(
        zoho_task_id="Z-MIX", todoist_task_id="N-MIX", orphan_check_count=1, provider="nirvana",
    )
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(return_value=_healthy_zoho_response("Z-MIX"))
    ctx["nirvana_client"].fetch = AsyncMock(side_effect=TodoistNotFoundError("not found"))
    _, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    mock_delete_zoho.assert_not_called()
    # session.get called to lock + increment.
    assert sess.get.await_count >= 1


@pytest.mark.asyncio
async def test_orphan_sweep_matched_provider_two_cycles_to_delete(fresh_settings, monkeypatch):
    """Sanity: when provider matches active provider, the original
    two-cycle confirmation still triggers deletion at count==1."""
    from app.worker.reconciler import orphan_sweep
    from app.todoist.client import TodoistNotFoundError

    monkeypatch.setenv("TASK_PROVIDER", "todoist")
    # No env override on mixed-provider cycles → default applies only to mismatched.

    state = _make_state(
        zoho_task_id="Z1", todoist_task_id="T1", orphan_check_count=1, provider="todoist",
    )
    factory, sess = _mock_session_factory_with_rows([state])
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(return_value=_healthy_zoho_response("Z1"))
    ctx["todoist_client"].fetch = AsyncMock(side_effect=TodoistNotFoundError("not found"))
    ctx["todoist_client"].delete = AsyncMock()
    _, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    mock_delete_zoho.assert_called_once()
    sess.delete.assert_awaited()


# ===========================================================================
# First-cycle threshold alert
# ===========================================================================

@pytest.mark.asyncio
async def test_orphan_sweep_first_cycle_threshold_alert_emitted(fresh_settings, monkeypatch, capsys):
    """More first-cycle rows than the configured threshold → warning
    containing the count is logged. Catches the 2026-10-20 Nirvana
    incident shape (76 rows in one sweep)."""
    from app.worker.reconciler import orphan_sweep
    from app.todoist.client import TodoistNotFoundError

    monkeypatch.setenv("TASK_PROVIDER", "todoist")
    monkeypatch.setenv("ZTS_ORPHAN_FIRST_CYCLE_ALERT_THRESHOLD", "2")

    # 4 first-cycle rows (rows with count==0 — never observed before). Use
    # matched provider so the default 2-cycle rule does NOT trigger
    # deletion — we're testing the alert, not deletion.
    rows = [
        _make_state(zoho_task_id=f"Z{i}", todoist_task_id=f"T{i}", orphan_check_count=0, provider="todoist")
        for i in range(4)
    ]
    factory, sess = _mock_session_factory_with_rows(rows)
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(return_value=_healthy_zoho_response("Z1"))
    ctx["todoist_client"].fetch = AsyncMock(side_effect=TodoistNotFoundError("not found"))
    _, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    # No rows should have been deleted (count=0 + matched-provider 2-cycle rule).
    mock_delete_zoho.assert_not_called()

    # structlog with the default PrintLoggerFactory writes to stdout, not
    # the stdlib logging system — assert via captured stdout.
    captured = capsys.readouterr()
    assert "orphan_sweep_first_cycle_threshold_exceeded" in captured.out, (
        f"threshold alert missing from stdout. out={captured.out!r}"
    )
    assert "first_cycle_count" in captured.out and "threshold=2" in captured.out


@pytest.mark.asyncio
async def test_orphan_sweep_first_cycle_threshold_disabled(fresh_settings, monkeypatch, capsys):
    """ZTS_ORPHAN_FIRST_CYCLE_ALERT_THRESHOLD=0 disables the alert — many
    first-cycle rows MUST NOT emit the threshold warning."""
    from app.worker.reconciler import orphan_sweep
    from app.todoist.client import TodoistNotFoundError

    monkeypatch.setenv("TASK_PROVIDER", "todoist")
    monkeypatch.setenv("ZTS_ORPHAN_FIRST_CYCLE_ALERT_THRESHOLD", "0")

    rows = [
        _make_state(zoho_task_id=f"Z{i}", todoist_task_id=f"T{i}", orphan_check_count=0, provider="todoist")
        for i in range(5)
    ]
    factory, sess = _mock_session_factory_with_rows(rows)
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(return_value=_healthy_zoho_response("Z1"))
    ctx["todoist_client"].fetch = AsyncMock(side_effect=TodoistNotFoundError("not found"))
    _, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    mock_delete_zoho.assert_not_called()
    captured = capsys.readouterr()
    assert "orphan_sweep_first_cycle_threshold_exceeded" not in captured.out, (
        "Threshold alert fired despite ZTS_ORPHAN_FIRST_CYCLE_ALERT_THRESHOLD=0"
    )


@pytest.mark.asyncio
async def test_orphan_sweep_first_cycle_below_threshold_no_alert(fresh_settings, monkeypatch, capsys):
    """First-cycle count below the threshold must not emit the alert."""
    from app.worker.reconciler import orphan_sweep
    from app.todoist.client import TodoistNotFoundError

    monkeypatch.setenv("TASK_PROVIDER", "todoist")
    monkeypatch.setenv("ZTS_ORPHAN_FIRST_CYCLE_ALERT_THRESHOLD", "10")

    rows = [
        _make_state(zoho_task_id=f"Z{i}", todoist_task_id=f"T{i}", orphan_check_count=0, provider="todoist")
        for i in range(3)
    ]
    factory, sess = _mock_session_factory_with_rows(rows)
    ctx = _make_reconciler_ctx()
    ctx["session_factory"] = factory

    ctx["zoho_client"].get_task = AsyncMock(return_value=_healthy_zoho_response("Z1"))
    ctx["todoist_client"].fetch = AsyncMock(side_effect=TodoistNotFoundError("not found"))
    _, mock_delete_zoho, _, _ = _common_orphan_patches(monkeypatch)

    await orphan_sweep(ctx)

    captured = capsys.readouterr()
    assert "orphan_sweep_first_cycle_threshold_exceeded" not in captured.out, (
        "Threshold alert fired below the threshold (3 <= 10)"
    )
