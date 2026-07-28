import json
from unittest.mock import AsyncMock, patch

import pytest

from app.nirvana.client import (
    NirvanaAPIError,
    NirvanaAuthError,
    NirvanaClient,
    NirvanaNotFoundError,
    NirvanaRateLimitError,
)
from app.core.normalise import NormalisedTask

BASE_URL = "https://mcp.nirvanahq.com/playground/run"


@pytest.mark.asyncio
async def test_call_tool_success(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_task_counts",
        method="POST",
        json={"ok": True, "tool": "get_task_counts", "args": {}, "result": {"next": 5}},
    )
    client = NirvanaClient(pat="test-pat")
    result = await client.call_tool("get_task_counts", {})
    assert result == {"next": 5}
    await client.close()


@pytest.mark.asyncio
async def test_call_tool_401_raises_auth_error(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/get_tasks", method="POST", status_code=401, json={})
    client = NirvanaClient(pat="bad-pat")
    with pytest.raises(NirvanaAuthError):
        await client.call_tool("get_tasks", {})
    await client.close()


@pytest.mark.asyncio
async def test_call_tool_404_raises_not_found(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/get_tasks", method="POST", status_code=404, json={})
    client = NirvanaClient(pat="pat")
    with pytest.raises(NirvanaNotFoundError):
        await client.call_tool("get_tasks", {})
    await client.close()


@pytest.mark.asyncio
async def test_call_tool_429_raises_rate_limit(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/get_tasks", method="POST", status_code=429, json={})
    client = NirvanaClient(pat="pat")
    with pytest.raises(NirvanaRateLimitError):
        await client.call_tool("get_tasks", {})
    await client.close()


@pytest.mark.asyncio
async def test_call_tool_500_raises_api_error(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/get_tasks", method="POST", status_code=500, json={})
    client = NirvanaClient(pat="pat")
    with pytest.raises(NirvanaAPIError):
        await client.call_tool("get_tasks", {})
    await client.close()


@pytest.mark.asyncio
async def test_call_tool_ok_false_raises_api_error(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={"ok": False, "error": "boom"},
    )
    client = NirvanaClient(pat="pat")
    with pytest.raises(NirvanaAPIError):
        await client.call_tool("get_tasks", {})
    await client.close()


@pytest.mark.asyncio
async def test_call_tool_sends_bearer_auth(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_task_counts",
        method="POST",
        json={"ok": True, "result": {}},
    )
    client = NirvanaClient(pat="secret-pat-value")
    await client.call_tool("get_task_counts", {})
    req = httpx_mock.get_request()
    assert req.headers["Authorization"] == "Bearer secret-pat-value"
    await client.close()


@pytest.mark.asyncio
async def test_get_tasks_calls_call_tool_with_filters(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={"ok": True, "result": [{"id": "1", "name": "Buy milk"}]},
    )
    client = NirvanaClient(pat="pat")
    tasks = await client.get_tasks(state="next", limit=200)
    assert tasks == [{"id": "1", "name": "Buy milk"}]
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"state": "next", "limit": 200}
    await client.close()


@pytest.mark.asyncio
async def test_get_tasks_handles_dict_result_with_tasks_key(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={"ok": True, "result": {"tasks": [{"id": "2"}]}},
    )
    client = NirvanaClient(pat="pat")
    tasks = await client.get_tasks()
    assert tasks == [{"id": "2"}]
    await client.close()


@pytest.mark.asyncio
async def test_update_tasks_uses_updates_top_level_key(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/update_tasks",
        method="POST",
        json={"ok": True, "result": {}},
    )
    client = NirvanaClient(pat="pat")
    await client.update_tasks([{"id": "1", "completed": True}])
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert "updates" in body
    assert body["updates"] == [{"id": "1", "completed": True}]
    assert "tasks" not in body
    await client.close()


@pytest.mark.asyncio
async def test_create_tasks_uses_tasks_top_level_key(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/create_tasks",
        method="POST",
        json={"ok": True, "result": [{"id": "3"}]},
    )
    client = NirvanaClient(pat="pat")
    await client.create_tasks([{"name": "New task"}])
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"tasks": [{"name": "New task"}]}
    await client.close()


@pytest.mark.asyncio
async def test_get_tags_calls_call_tool(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tags",
        method="POST",
        json={"ok": True, "result": [{"name": "work"}]},
    )
    client = NirvanaClient(pat="pat")
    result = await client.get_tags()
    assert result == [{"name": "work"}]
    await client.close()


@pytest.mark.asyncio
async def test_get_task_counts_calls_call_tool(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_task_counts",
        method="POST",
        json={"ok": True, "result": {"next": 3}},
    )
    client = NirvanaClient(pat="pat")
    result = await client.get_task_counts()
    assert result == {"next": 3}
    await client.close()


# --- Task 3: Protocol-conformant methods ---


@pytest.mark.asyncio
async def test_fetch_returns_normalised_task(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={
            "ok": True,
            "result": [
                {"id": "other", "name": "Other", "state": "next", "starred": False},
                {"id": "N1", "name": "Buy milk", "state": "next", "starred": True, "duedate": "2026-08-01"},
            ],
        },
    )
    client = NirvanaClient(pat="pat")
    result = await client.fetch("N1")
    assert isinstance(result, NormalisedTask)
    assert result.title == "Buy milk"
    assert result.due_date == "2026-08-01"
    await client.close()


@pytest.mark.asyncio
async def test_fetch_missing_id_raises_not_found(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={"ok": True, "result": [{"id": "other", "name": "Other"}]},
    )
    client = NirvanaClient(pat="pat")
    with pytest.raises(NirvanaNotFoundError):
        await client.fetch("N-missing")
    await client.close()


@pytest.mark.asyncio
async def test_fetch_scopes_to_zoho_tag(httpx_mock):
    """2026-07-28: fetch() must filter by tags=["Zoho"], not scan the whole
    account — confirmed live that an unfiltered single-page scan silently
    missed tasks once the account exceeded 200 total items."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        match_json={"tags": ["Zoho"], "limit": NirvanaClient.FETCH_PAGE_SIZE, "offset": 0},
        json={"ok": True, "result": {"tasks": [{"id": "N1", "name": "Buy milk", "state": "next"}], "has_more": False}},
    )
    client = NirvanaClient(pat="pat")
    result = await client.fetch("N1")
    assert result.title == "Buy milk"
    await client.close()


@pytest.mark.asyncio
async def test_fetch_paginates_across_multiple_pages(httpx_mock):
    """Confirmed live 2026-07-28: get_tasks supports real offset/has_more
    pagination — fetch() must follow it rather than giving up after one page."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        match_json={"tags": ["Zoho"], "limit": NirvanaClient.FETCH_PAGE_SIZE, "offset": 0},
        json={"ok": True, "result": {"tasks": [{"id": "N1", "name": "Page 1 task"}], "has_more": True}},
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        match_json={"tags": ["Zoho"], "limit": NirvanaClient.FETCH_PAGE_SIZE, "offset": NirvanaClient.FETCH_PAGE_SIZE},
        json={"ok": True, "result": {"tasks": [{"id": "N2", "name": "Page 2 task"}], "has_more": False}},
    )
    client = NirvanaClient(pat="pat")
    result = await client.fetch("N2")
    assert result.title == "Page 2 task"
    await client.close()


@pytest.mark.asyncio
async def test_get_tasks_paginated_stops_at_max_pages_safety_cap():
    """Defensive cap: never loop forever even if the API always reports
    has_more=True (e.g. a bug on Nirvana's side)."""
    client = NirvanaClient(pat="pat")
    call_count = 0

    async def fake_call_tool(tool, args):
        nonlocal call_count
        call_count += 1
        return {"tasks": [{"id": f"N{call_count}"}], "has_more": True}

    with patch.object(client, "call_tool", side_effect=fake_call_tool):
        tasks = await client.get_tasks_paginated(page_size=10, max_pages=3)

    assert call_count == 3
    assert len(tasks) == 3
    await client.close()


@pytest.mark.asyncio
async def test_get_tasks_paginated_stops_when_page_returns_no_tasks():
    """A page returning zero tasks (even with a stale has_more=True) signals
    exhaustion — must not loop past it."""
    client = NirvanaClient(pat="pat")
    call_count = 0

    async def fake_call_tool(tool, args):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"tasks": [{"id": "N1"}], "has_more": True}
        return {"tasks": [], "has_more": True}  # stale has_more, no items -> stop

    with patch.object(client, "call_tool", side_effect=fake_call_tool):
        tasks = await client.get_tasks_paginated(page_size=10, max_pages=25)

    assert call_count == 2
    assert len(tasks) == 1
    await client.close()


@pytest.mark.asyncio
async def test_create_delegates_to_create_nirvana_task_and_accepts_description():
    """description becomes the Nirvana task's note (2026-07-28 decision)."""
    client = NirvanaClient(pat="pat")
    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    with patch("app.nirvana.writer.create_nirvana_task", new=AsyncMock(return_value="N9")) as mocked:
        result = await client.create(normalised, "Z1", description="x")
    mocked.assert_awaited_once_with(normalised, "Z1", client, note="x")
    assert result == "N9"


@pytest.mark.asyncio
async def test_update_delegates_to_update_nirvana_task():
    client = NirvanaClient(pat="pat")
    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    with patch("app.nirvana.writer.update_nirvana_task", new=AsyncMock()) as mocked:
        await client.update("N1", normalised)
    mocked.assert_awaited_once_with("N1", normalised, client)


@pytest.mark.asyncio
async def test_complete_delegates_to_complete_nirvana_task():
    client = NirvanaClient(pat="pat")
    with patch("app.nirvana.writer.complete_nirvana_task", new=AsyncMock()) as mocked:
        await client.complete("N1")
    mocked.assert_awaited_once_with("N1", client)


@pytest.mark.asyncio
async def test_delete_delegates_to_delete_nirvana_task():
    client = NirvanaClient(pat="pat")
    with patch("app.nirvana.writer.delete_nirvana_task", new=AsyncMock()) as mocked:
        await client.delete("N1", task_name="Buy milk")
    mocked.assert_awaited_once_with("N1", client, task_name="Buy milk")
