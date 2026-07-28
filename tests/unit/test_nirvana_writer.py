import pytest
import resend
from unittest.mock import AsyncMock

from app.nirvana.client import NirvanaAPIError
from app.nirvana.writer import (
    complete_nirvana_task,
    create_nirvana_task,
    delete_nirvana_task,
    update_nirvana_task,
)
from app.core.normalise import NormalisedTask


@pytest.mark.asyncio
async def test_create_nirvana_task_always_inbox_unstarred_ignoring_priority(complete_env):
    """2026-07-28 decision: Zoho priority is ignored entirely for Nirvana —
    every created task lands in state=inbox, unstarred, regardless of the
    normalised priority value."""
    mock_client = AsyncMock()
    mock_client.create_tasks = AsyncMock(return_value=[{"id": "N1"}])

    normalised = NormalisedTask(title="Buy milk", due_date="2026-08-01", priority=4, is_completed=False)
    result = await create_nirvana_task(normalised, zoho_task_id="Z1", client=mock_client)

    assert result == "N1"
    items = mock_client.create_tasks.call_args.args[0]
    assert items == [{"name": "Buy milk", "state": "inbox", "starred": False, "duedate": "2026-08-01"}]


@pytest.mark.asyncio
async def test_create_nirvana_task_includes_note_when_provided(complete_env):
    mock_client = AsyncMock()
    mock_client.create_tasks = AsyncMock(return_value=[{"id": "N1"}])

    normalised = NormalisedTask(title="Buy milk", due_date=None, priority=1, is_completed=False)
    await create_nirvana_task(normalised, zoho_task_id="Z1", client=mock_client, note="[Deal](url)")

    items = mock_client.create_tasks.call_args.args[0]
    assert items[0]["note"] == "[Deal](url)"


@pytest.mark.asyncio
async def test_create_nirvana_task_omits_note_when_none(complete_env):
    mock_client = AsyncMock()
    mock_client.create_tasks = AsyncMock(return_value=[{"id": "N1"}])

    normalised = NormalisedTask(title="Buy milk", due_date=None, priority=1, is_completed=False)
    await create_nirvana_task(normalised, zoho_task_id="Z1", client=mock_client)

    items = mock_client.create_tasks.call_args.args[0]
    assert "note" not in items[0]


@pytest.mark.asyncio
async def test_create_nirvana_task_omits_duedate_when_none(complete_env):
    mock_client = AsyncMock()
    mock_client.create_tasks = AsyncMock(return_value=[{"id": "N2"}])

    normalised = NormalisedTask(title="No due", due_date=None, priority=1, is_completed=False)
    await create_nirvana_task(normalised, zoho_task_id="Z2", client=mock_client)

    items = mock_client.create_tasks.call_args.args[0]
    assert "duedate" not in items[0]


@pytest.mark.asyncio
async def test_create_nirvana_task_parses_list_result(complete_env):
    mock_client = AsyncMock()
    mock_client.create_tasks = AsyncMock(return_value=[{"id": "N3"}])

    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    result = await create_nirvana_task(normalised, zoho_task_id="Z3", client=mock_client)

    assert result == "N3"


@pytest.mark.asyncio
async def test_create_nirvana_task_parses_dict_tasks_key_result(complete_env):
    mock_client = AsyncMock()
    mock_client.create_tasks = AsyncMock(return_value={"tasks": [{"id": "N4"}]})

    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    result = await create_nirvana_task(normalised, zoho_task_id="Z4", client=mock_client)

    assert result == "N4"


@pytest.mark.asyncio
async def test_create_nirvana_task_unparseable_result_raises(complete_env):
    mock_client = AsyncMock()
    mock_client.create_tasks = AsyncMock(return_value={"unexpected": "shape"})

    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    with pytest.raises(NirvanaAPIError):
        await create_nirvana_task(normalised, zoho_task_id="Z5", client=mock_client)


@pytest.mark.asyncio
async def test_update_nirvana_task_includes_duedate_when_present(complete_env):
    """2026-07-28 decision: update never sends state/starred — Nirvana's GTD
    state/star is fully user-owned once a task lands in the inbox."""
    mock_client = AsyncMock()
    mock_client.update_tasks = AsyncMock(return_value={})

    normalised = NormalisedTask(title="Updated", due_date="2026-09-01", priority=3, is_completed=False)
    await update_nirvana_task("N1", normalised, client=mock_client)

    updates = mock_client.update_tasks.call_args.args[0]
    assert updates == [{"id": "N1", "name": "Updated", "duedate": "2026-09-01"}]
    assert "state" not in updates[0]
    assert "starred" not in updates[0]


@pytest.mark.asyncio
async def test_update_nirvana_task_clears_duedate_when_none(complete_env):
    mock_client = AsyncMock()
    mock_client.update_tasks = AsyncMock(return_value={})

    normalised = NormalisedTask(title="No due", due_date=None, priority=1, is_completed=False)
    await update_nirvana_task("N1", normalised, client=mock_client)

    updates = mock_client.update_tasks.call_args.args[0]
    assert updates[0]["duedate"] == ""


@pytest.mark.asyncio
async def test_complete_nirvana_task_sends_completed_true(complete_env):
    mock_client = AsyncMock()
    mock_client.update_tasks = AsyncMock(return_value={})

    await complete_nirvana_task("N1", client=mock_client)

    updates = mock_client.update_tasks.call_args.args[0]
    assert updates == [{"id": "N1", "completed": True}]


@pytest.mark.asyncio
async def test_delete_nirvana_task_sends_trash_state_and_notification(complete_env, monkeypatch):
    mock_client = AsyncMock()
    mock_client.update_tasks = AsyncMock(return_value={})

    sent = []

    async def fake(params):
        sent.append(params)

    monkeypatch.setattr("resend.Emails.send_async", fake)
    monkeypatch.setattr(resend, "api_key", "re_test_key")

    await delete_nirvana_task("N1", client=mock_client, task_name="Buy milk")

    updates = mock_client.update_tasks.call_args.args[0]
    assert updates == [{"id": "N1", "state": "trash"}]
    assert len(sent) == 1
    assert "Buy milk" in sent[0]["subject"] or "Buy milk" in sent[0]["html"]


@pytest.mark.asyncio
async def test_delete_nirvana_task_resend_failure_does_not_raise(complete_env, monkeypatch):
    mock_client = AsyncMock()
    mock_client.update_tasks = AsyncMock(return_value={})

    async def boom(p):
        raise RuntimeError("resend down")

    monkeypatch.setattr("resend.Emails.send_async", boom)

    # MUST NOT raise
    await delete_nirvana_task("N1", client=mock_client)
