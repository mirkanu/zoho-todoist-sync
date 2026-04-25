"""Unit tests for scripts.migrate — migration algorithm.

Covers: SEED-1 (idempotency), SEED-2 (create-for-empty-id), SEED-3 (description replaced),
D-02 (dry-run), D-03 (404 fallback).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, call
from datetime import datetime, timezone

from app.core.hash import canonical_hash
from app.zoho.normalise import zoho_record_to_normalised


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_RECORD_WITH_ID = {
    "id": "ZOHO-123",
    "Subject": "Test task",
    "Due_Date": "2026-05-01",
    "Priority": "High",
    "Status": "In Progress",
    "Todoist_Task_ID": "TD-456",
}

SAMPLE_RECORD_NO_ID = {
    "id": "ZOHO-999",
    "Subject": "New task",
    "Due_Date": None,
    "Priority": "Normal",
    "Status": "In Progress",
    "Todoist_Task_ID": None,
}

TERMINAL_STATUSES = ["Completed"]
ZOHO_FIELD_API_NAME = "Todoist_Task_ID"
ACCESS_TOKEN = "test-access-token"


def _make_session_factory_existing_state(existing: object | None):
    """Build a session_factory mock where scalar_one_or_none returns `existing`."""
    sess = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=existing)
    result.scalar_one = MagicMock(return_value=existing)
    sess.execute = AsyncMock(return_value=result)
    sess.add = MagicMock()
    sess.commit = AsyncMock()
    sess.begin = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    ))
    ctx = AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    )
    factory = MagicMock(return_value=ctx)
    return factory, sess


def _make_todoist_client(fetch_result=None, fetch_raises=None):
    """Build a minimal TodoistClient mock."""
    client = MagicMock()
    if fetch_raises:
        client.fetch_todoist_task = AsyncMock(side_effect=fetch_raises)
    else:
        client.fetch_todoist_task = AsyncMock(return_value=fetch_result or MagicMock())
    # _api is accessed directly for update_task
    client._api = AsyncMock()
    client._api.update_task = AsyncMock()
    return client


def _make_counters():
    return {"already_linked": 0, "linked": 0, "created": 0, "recreated": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Test 1: Already linked → skip (SEED-1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_already_linked_skipped(complete_env, monkeypatch):
    """When SyncState row exists, migrate_one_task skips all writes."""
    from scripts.migrate import migrate_one_task
    from app.todoist.client import TodoistNotFoundError

    existing_state = MagicMock()  # non-None => already linked
    session_factory, sess = _make_session_factory_existing_state(existing_state)
    todoist_client = _make_todoist_client()
    counters = _make_counters()

    mock_write_back = AsyncMock()
    monkeypatch.setattr("scripts.migrate.write_todoist_id_to_zoho", mock_write_back)
    mock_create = AsyncMock(return_value="new-td-id")
    monkeypatch.setattr("scripts.migrate.create_todoist_task", mock_create)

    await migrate_one_task(
        record=SAMPLE_RECORD_WITH_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory,
        counters=counters,
        dry_run=False,
    )

    assert counters["already_linked"] == 1
    assert counters["linked"] == 0
    assert counters["created"] == 0
    todoist_client.fetch_todoist_task.assert_not_called()
    todoist_client._api.update_task.assert_not_called()
    mock_create.assert_not_called()
    mock_write_back.assert_not_called()
    sess.add.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Link existing pair (SEED-3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_link_existing_pair(complete_env, monkeypatch):
    """Zoho has Todoist_Task_ID; fetch succeeds; migration calls update_task with footer."""
    from scripts.migrate import migrate_one_task

    session_factory, sess = _make_session_factory_existing_state(None)  # no existing state
    todoist_client = _make_todoist_client()
    counters = _make_counters()

    mock_write_back = AsyncMock()
    monkeypatch.setattr("scripts.migrate.write_todoist_id_to_zoho", mock_write_back)

    await migrate_one_task(
        record=SAMPLE_RECORD_WITH_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory,
        counters=counters,
        dry_run=False,
    )

    assert counters["linked"] == 1
    todoist_client.fetch_todoist_task.assert_called_once_with("TD-456")
    todoist_client._api.update_task.assert_called_once()
    kwargs = todoist_client._api.update_task.call_args.kwargs
    assert kwargs["description"] == "\n\n---\n[zoho:ZOHO-123]"
    # write_todoist_id_to_zoho should NOT be called for existing pair
    mock_write_back.assert_not_called()
    sess.add.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: Description replaced not appended (SEED-3 exact-match guard)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_description_replaced_not_appended(complete_env, monkeypatch):
    """The description arg passed to update_task is EXACTLY the footer — no preamble."""
    from scripts.migrate import migrate_one_task

    session_factory, sess = _make_session_factory_existing_state(None)
    todoist_client = _make_todoist_client()
    counters = _make_counters()

    monkeypatch.setattr("scripts.migrate.write_todoist_id_to_zoho", AsyncMock())

    await migrate_one_task(
        record=SAMPLE_RECORD_WITH_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory,
        counters=counters,
        dry_run=False,
    )

    todoist_client._api.update_task.assert_called_once()
    call_args = todoist_client._api.update_task.call_args
    # description must be EXACTLY this string — not a substring check
    assert call_args.kwargs["description"] == "\n\n---\n[zoho:ZOHO-123]"
    # Guard: should not contain any Make.com preamble text
    desc = call_args.kwargs["description"]
    assert desc.startswith("\n\n---\n[zoho:"), f"Unexpected prefix in description: {desc!r}"
    assert desc == "\n\n---\n[zoho:ZOHO-123]", f"Description not exact: {desc!r}"


# ---------------------------------------------------------------------------
# Test 4: Todoist 404 fallback (D-03)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_todoist_404_fallback(complete_env, monkeypatch):
    """fetch_todoist_task raises TodoistNotFoundError → create new task, write back, upsert."""
    from scripts.migrate import migrate_one_task
    from app.todoist.client import TodoistNotFoundError

    session_factory, sess = _make_session_factory_existing_state(None)
    todoist_client = _make_todoist_client(fetch_raises=TodoistNotFoundError("404"))
    counters = _make_counters()

    mock_create = AsyncMock(return_value="new-td-id")
    mock_write_back = AsyncMock()
    monkeypatch.setattr("scripts.migrate.create_todoist_task", mock_create)
    monkeypatch.setattr("scripts.migrate.write_todoist_id_to_zoho", mock_write_back)

    # Should NOT raise even though fetch failed
    await migrate_one_task(
        record=SAMPLE_RECORD_WITH_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory,
        counters=counters,
        dry_run=False,
    )

    assert counters["recreated"] == 1
    assert counters["linked"] == 0
    mock_create.assert_called_once()
    mock_write_back.assert_called_once_with("ZOHO-123", "new-td-id", ACCESS_TOKEN)
    sess.add.assert_called_once()
    # update_task should NOT be called (we created a new task instead)
    todoist_client._api.update_task.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: Create for empty Todoist_Task_ID (SEED-2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_for_empty_id(complete_env, monkeypatch):
    """Zoho task has no Todoist_Task_ID → create fresh, write back, store sync_state."""
    from scripts.migrate import migrate_one_task

    session_factory, sess = _make_session_factory_existing_state(None)
    todoist_client = _make_todoist_client()
    counters = _make_counters()

    mock_create = AsyncMock(return_value="new-td-id")
    mock_write_back = AsyncMock()
    monkeypatch.setattr("scripts.migrate.create_todoist_task", mock_create)
    monkeypatch.setattr("scripts.migrate.write_todoist_id_to_zoho", mock_write_back)

    await migrate_one_task(
        record=SAMPLE_RECORD_NO_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory,
        counters=counters,
        dry_run=False,
    )

    assert counters["created"] == 1
    mock_create.assert_called_once()
    mock_write_back.assert_called_once_with("ZOHO-999", "new-td-id", ACCESS_TOKEN)
    sess.add.assert_called_once()
    # fetch_todoist_task should not be called — no ID to fetch
    todoist_client.fetch_todoist_task.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: Dry run — no writes (D-02)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_no_writes(complete_env, monkeypatch):
    """With dry_run=True, no writes occur; counters still increment."""
    from scripts.migrate import migrate_one_task

    session_factory, sess = _make_session_factory_existing_state(None)
    todoist_client = _make_todoist_client()
    counters = _make_counters()

    mock_create = AsyncMock(return_value="new-td-id")
    mock_write_back = AsyncMock()
    monkeypatch.setattr("scripts.migrate.create_todoist_task", mock_create)
    monkeypatch.setattr("scripts.migrate.write_todoist_id_to_zoho", mock_write_back)

    # Zoho task WITH existing Todoist_Task_ID (link path)
    await migrate_one_task(
        record=SAMPLE_RECORD_WITH_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory,
        counters=counters,
        dry_run=True,
    )

    assert counters["linked"] == 1
    # No writes
    todoist_client._api.update_task.assert_not_called()
    mock_write_back.assert_not_called()
    sess.add.assert_not_called()

    # Also test create path with dry_run
    counters2 = _make_counters()
    session_factory2, sess2 = _make_session_factory_existing_state(None)
    await migrate_one_task(
        record=SAMPLE_RECORD_NO_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory2,
        counters=counters2,
        dry_run=True,
    )
    assert counters2["created"] == 1
    mock_create.assert_not_called()
    mock_write_back.assert_not_called()
    sess2.add.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7: Dry run prints counts (D-02)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_prints_counts(complete_env, monkeypatch, capsys):
    """run_migration with dry_run=True prints all counter labels to stdout."""
    from scripts.migrate import run_migration

    session_factory, sess = _make_session_factory_existing_state(None)
    todoist_client = _make_todoist_client()

    mock_create = AsyncMock(return_value="new-td-id")
    mock_write_back = AsyncMock()
    monkeypatch.setattr("scripts.migrate.create_todoist_task", mock_create)
    monkeypatch.setattr("scripts.migrate.write_todoist_id_to_zoho", mock_write_back)

    records = [SAMPLE_RECORD_WITH_ID, SAMPLE_RECORD_NO_ID]

    await run_migration(
        records=records,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory,
        dry_run=True,
    )

    captured = capsys.readouterr()
    assert "linked" in captured.out
    assert "created" in captured.out
    assert "recreated" in captured.out
    assert "already_linked" in captured.out


# ---------------------------------------------------------------------------
# Test 8: Canonical hash seeded correctly (SEED-2 / hash correctness)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_canonical_hash_seeded(complete_env, monkeypatch):
    """When sync_state is upserted, last_hash equals canonical_hash(normalised record)."""
    from scripts.migrate import migrate_one_task
    from app.db.models import SyncState

    session_factory, sess = _make_session_factory_existing_state(None)
    todoist_client = _make_todoist_client()
    counters = _make_counters()

    mock_write_back = AsyncMock()
    monkeypatch.setattr("scripts.migrate.write_todoist_id_to_zoho", mock_write_back)

    await migrate_one_task(
        record=SAMPLE_RECORD_WITH_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory,
        counters=counters,
        dry_run=False,
    )

    # Compute expected hash
    normalised = zoho_record_to_normalised(SAMPLE_RECORD_WITH_ID, TERMINAL_STATUSES)
    expected_hash = canonical_hash(normalised)

    # Verify session.add was called with a SyncState having the correct hash
    sess.add.assert_called_once()
    added_obj = sess.add.call_args.args[0]
    assert isinstance(added_obj, SyncState)
    assert added_obj.last_hash == expected_hash
    assert added_obj.zoho_task_id == "ZOHO-123"
    assert added_obj.todoist_task_id == "TD-456"


# ---------------------------------------------------------------------------
# Test 9: Idempotency — second run skips (SEED-1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotency_two_runs(complete_env, monkeypatch):
    """Invoking migrate_one_task twice for same record results in zero writes on second call."""
    from scripts.migrate import migrate_one_task
    from app.db.models import SyncState

    # First run: no existing state
    session_factory_1, sess_1 = _make_session_factory_existing_state(None)
    todoist_client = _make_todoist_client()
    counters_1 = _make_counters()

    mock_write_back = AsyncMock()
    mock_create = AsyncMock(return_value="new-td-id")
    monkeypatch.setattr("scripts.migrate.write_todoist_id_to_zoho", mock_write_back)
    monkeypatch.setattr("scripts.migrate.create_todoist_task", mock_create)

    await migrate_one_task(
        record=SAMPLE_RECORD_WITH_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory_1,
        counters=counters_1,
        dry_run=False,
    )
    assert counters_1["linked"] == 1
    assert sess_1.add.call_count == 1

    # Second run: now existing state exists (simulate idempotency)
    existing_state = MagicMock()  # non-None
    session_factory_2, sess_2 = _make_session_factory_existing_state(existing_state)
    counters_2 = _make_counters()
    # Reset call counts on shared mocks
    todoist_client.fetch_todoist_task.reset_mock()
    todoist_client._api.update_task.reset_mock()

    await migrate_one_task(
        record=SAMPLE_RECORD_WITH_ID,
        zoho_field_api_name=ZOHO_FIELD_API_NAME,
        terminal_statuses=TERMINAL_STATUSES,
        access_token=ACCESS_TOKEN,
        todoist_client=todoist_client,
        session_factory=session_factory_2,
        counters=counters_2,
        dry_run=False,
    )
    assert counters_2["already_linked"] == 1
    # Zero writes on second run
    todoist_client.fetch_todoist_task.assert_not_called()
    todoist_client._api.update_task.assert_not_called()
    sess_2.add.assert_not_called()
