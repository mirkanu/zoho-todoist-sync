import pytest
import httpx
import resend
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from app.todoist.writer import (
    create_todoist_task, update_todoist_task,
    complete_todoist_task, delete_todoist_task,
)
from app.todoist.client import (
    TodoistAuthError, TodoistNotFoundError,
    TodoistRateLimitError, TodoistAPIError,
)
from app.core.normalise import NormalisedTask


@pytest.mark.asyncio
async def test_create_todoist_task_builds_correct_payload(complete_env):
    """create builds correct payload with due_date as date object and priority; no description."""
    mock_api = AsyncMock()
    mock_task = MagicMock()
    mock_task.id = "T999"
    mock_api.add_task = AsyncMock(return_value=mock_task)

    normalised = NormalisedTask(title="Do thing", due_date="2026-05-01", priority=3, is_completed=False)
    result = await create_todoist_task(normalised, zoho_task_id="Z111", todoist_api=mock_api)

    assert result == "T999"
    call_kwargs = mock_api.add_task.call_args.kwargs
    assert call_kwargs["content"] == "Do thing"
    assert "description" not in call_kwargs
    assert call_kwargs["priority"] == 3
    assert isinstance(call_kwargs["due_date"], date)
    assert "due_datetime" not in call_kwargs  # SYNC-2 hard constraint
    assert "labels" not in call_kwargs  # SYNC-9
    assert call_kwargs["project_id"] == complete_env["TODOIST_PROJECT_ID"]


@pytest.mark.asyncio
async def test_create_todoist_task_none_due_date_passes_none(complete_env):
    """SYNC-2: when no due_date, pass due_date=None — NOT due_string='no date' for creation."""
    mock_api = AsyncMock()
    mock_task = MagicMock()
    mock_task.id = "T1"
    mock_api.add_task = AsyncMock(return_value=mock_task)

    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    await create_todoist_task(normalised, zoho_task_id="Z1", todoist_api=mock_api)

    call_kwargs = mock_api.add_task.call_args.kwargs
    assert call_kwargs["due_date"] is None
    assert "due_string" not in call_kwargs  # creation path does NOT send "no date"


@pytest.mark.asyncio
async def test_create_todoist_task_401_raises_auth_error(complete_env):
    """Error taxonomy: 401 -> TodoistAuthError."""
    mock_api = AsyncMock()
    err = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=MagicMock(status_code=401)
    )
    mock_api.add_task.side_effect = err

    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    with pytest.raises(TodoistAuthError):
        await create_todoist_task(normalised, zoho_task_id="Z1", todoist_api=mock_api)


@pytest.mark.asyncio
async def test_create_todoist_task_429_raises_rate_limit(complete_env):
    """Error taxonomy: 429 -> TodoistRateLimitError."""
    mock_api = AsyncMock()
    err = httpx.HTTPStatusError(
        "429", request=MagicMock(), response=MagicMock(status_code=429)
    )
    mock_api.add_task.side_effect = err

    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    with pytest.raises(TodoistRateLimitError):
        await create_todoist_task(normalised, zoho_task_id="Z1", todoist_api=mock_api)


@pytest.mark.asyncio
async def test_update_todoist_task_with_due_date(complete_env):
    """SYNC-2: update with due_date passes date object, no description, no labels."""
    mock_api = AsyncMock()
    mock_api.update_task = AsyncMock(return_value=MagicMock())

    normalised = NormalisedTask(title="Updated title", due_date="2026-06-01", priority=4, is_completed=False)
    await update_todoist_task("T1", normalised, todoist_api=mock_api)

    call_kwargs = mock_api.update_task.call_args.kwargs
    assert call_kwargs["content"] == "Updated title"
    assert call_kwargs["priority"] == 4
    assert call_kwargs["due_date"] == date(2026, 6, 1)
    assert "due_string" not in call_kwargs
    assert "description" not in call_kwargs  # Pitfall 6
    assert "labels" not in call_kwargs  # SYNC-9


@pytest.mark.asyncio
async def test_update_todoist_task_clears_due_date(complete_env):
    """EDGE-3 + Pitfall 1: None due_date uses due_string='no date' (not due_date=None)."""
    mock_api = AsyncMock()
    mock_api.update_task = AsyncMock(return_value=MagicMock())

    normalised = NormalisedTask(title="No due", due_date=None, priority=1, is_completed=False)
    await update_todoist_task("T1", normalised, todoist_api=mock_api)

    call_kwargs = mock_api.update_task.call_args.kwargs
    assert call_kwargs["due_string"] == "no date"
    assert "due_date" not in call_kwargs  # must NOT pass due_date=None — SDK drops it


@pytest.mark.asyncio
async def test_complete_todoist_task_calls_sdk(complete_env):
    """EDGE-7: complete closes the task via SDK complete_task."""
    mock_api = AsyncMock()
    mock_api.complete_task = AsyncMock(return_value=True)

    await complete_todoist_task("T1", todoist_api=mock_api)

    assert mock_api.complete_task.await_count == 1
    assert mock_api.complete_task.await_args == (("T1",), {})


@pytest.mark.asyncio
async def test_delete_todoist_task_sends_email(complete_env, monkeypatch):
    """EDGE-1: delete calls SDK and sends Resend email to manuelkuhs@gmail.com."""
    mock_api = AsyncMock()
    mock_api.delete_task = AsyncMock(return_value=True)

    sent = []

    async def fake(params):
        sent.append(params)

    monkeypatch.setattr("resend.Emails.send_async", fake)
    monkeypatch.setattr(resend, "api_key", "re_test_key")

    await delete_todoist_task("T1", todoist_api=mock_api)

    assert mock_api.delete_task.await_count == 1
    assert len(sent) == 1
    assert sent[0]["to"] == ["manuelkuhs@gmail.com"]
    assert "T1" in sent[0]["subject"] or "T1" in sent[0]["html"]


@pytest.mark.asyncio
async def test_delete_todoist_task_resend_failure_does_not_raise(complete_env, monkeypatch):
    """EDGE-6: Resend failure is caught and logged; does NOT re-raise."""
    mock_api = AsyncMock()
    mock_api.delete_task = AsyncMock(return_value=True)

    async def boom(p):
        raise RuntimeError("resend down")

    monkeypatch.setattr("resend.Emails.send_async", boom)

    # MUST NOT raise
    await delete_todoist_task("T1", todoist_api=mock_api)


@pytest.mark.asyncio
async def test_delete_todoist_task_idempotent_when_already_gone(complete_env, monkeypatch):
    """Idempotency + Pitfall 5: 404 on delete returns early without sending email."""
    mock_api = AsyncMock()
    err = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock(status_code=404)
    )
    mock_api.delete_task.side_effect = err

    sent = []

    async def fake(params):
        sent.append(params)

    monkeypatch.setattr("resend.Emails.send_async", fake)

    result = await delete_todoist_task("T1", todoist_api=mock_api)

    assert result is None  # returned normally, not raised
    assert len(sent) == 0  # no duplicate email when task already gone


@pytest.mark.asyncio
async def test_delete_todoist_task_non_404_error_raises(complete_env, monkeypatch):
    """Error taxonomy: non-404 on delete raises typed exception; no email sent."""
    mock_api = AsyncMock()
    err = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=MagicMock(status_code=401)
    )
    mock_api.delete_task.side_effect = err

    sent = []

    async def fake(params):
        sent.append(params)

    monkeypatch.setattr("resend.Emails.send_async", fake)

    with pytest.raises(TodoistAuthError):
        await delete_todoist_task("T1", todoist_api=mock_api)

    assert len(sent) == 0  # no email when error


@pytest.mark.asyncio
async def test_create_todoist_task_idempotency_same_inputs(complete_env):
    """Idempotency: writer does NOT cache/dedup internally; caller is responsible via sync_state."""
    mock_api = AsyncMock()
    mock_task = MagicMock()
    mock_task.id = "T999"
    mock_api.add_task = AsyncMock(return_value=mock_task)

    normalised = NormalisedTask(title="Same task", due_date="2026-05-01", priority=2, is_completed=False)

    result1 = await create_todoist_task(normalised, zoho_task_id="Z1", todoist_api=mock_api)
    result2 = await create_todoist_task(normalised, zoho_task_id="Z1", todoist_api=mock_api)

    # Writer calls through twice — deduplication is caller responsibility
    assert mock_api.add_task.await_count == 2
    assert result1 == result2 == "T999"


@pytest.mark.asyncio
async def test_create_todoist_task_with_description(complete_env):
    """DESC-1+2: when description provided, add_task receives description kwarg."""
    mock_api = AsyncMock()
    mock_task = MagicMock()
    mock_task.id = "T888"
    mock_api.add_task = AsyncMock(return_value=mock_task)

    normalised = NormalisedTask(title="Rich task", due_date=None, priority=2, is_completed=False)
    desc = "Re: Acme Deal\nhttps://crm.zoho.eu/crm/org20100156718/tab/Tasks/Z1\nNot synced back to Zoho."
    result = await create_todoist_task(normalised, zoho_task_id="Z1", todoist_api=mock_api, description=desc)

    assert result == "T888"
    call_kwargs = mock_api.add_task.call_args.kwargs
    assert call_kwargs["description"] == desc


@pytest.mark.asyncio
async def test_create_todoist_task_no_description_omits_kwarg(complete_env):
    """DESC-5 (create path): when description=None (default), description kwarg absent from add_task."""
    mock_api = AsyncMock()
    mock_task = MagicMock()
    mock_task.id = "T777"
    mock_api.add_task = AsyncMock(return_value=mock_task)

    normalised = NormalisedTask(title="Plain task", due_date=None, priority=1, is_completed=False)
    await create_todoist_task(normalised, zoho_task_id="Z2", todoist_api=mock_api)

    call_kwargs = mock_api.add_task.call_args.kwargs
    assert "description" not in call_kwargs
