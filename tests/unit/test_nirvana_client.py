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
    """get_tasks forwards filters as the call_tool args verbatim and parses
    the real groups[].tasks[] response shape (confirmed live 2026-10-20)."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={
            "ok": True,
            "result": {
                "groups": [
                    {"tasks": [{"id": "1", "name": "Buy milk"}]},
                ],
                "total": 1,
                "returned": 1,
                "has_more": False,
            },
        },
    )
    client = NirvanaClient(pat="pat")
    tasks = await client.get_tasks(state="next", limit=200)
    assert tasks == [{"id": "1", "name": "Buy milk"}]
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"state": "next", "limit": 200}
    await client.close()


@pytest.mark.asyncio
async def test_get_tasks_walks_multiple_groups(httpx_mock):
    """Real Nirvana responses can carry more than one group (e.g. one per
    project/area). All tasks across all groups must be collected."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={
            "ok": True,
            "result": {
                "groups": [
                    {"tasks": [{"id": "1", "name": "First"}]},
                    {"tasks": [{"id": "2", "name": "Second"}, {"id": "3", "name": "Third"}]},
                    {"tasks": []},  # empty group, must be skipped
                ],
                "has_more": False,
            },
        },
    )
    client = NirvanaClient(pat="pat")
    tasks = await client.get_tasks()
    assert [t["id"] for t in tasks] == ["1", "2", "3"]
    await client.close()


def test_extract_tasks_from_result_handles_non_dict():
    """_extract_tasks_from_result must never raise — defensive against any
    future shape change on Nirvana's side."""
    assert NirvanaClient._extract_tasks_from_result(None) == []
    assert NirvanaClient._extract_tasks_from_result([]) == []
    assert NirvanaClient._extract_tasks_from_result("oops") == []
    assert NirvanaClient._extract_tasks_from_result({}) == []
    assert NirvanaClient._extract_tasks_from_result({"groups": None}) == []
    assert NirvanaClient._extract_tasks_from_result({"groups": "no"}) == []


def test_extract_tasks_from_result_skips_non_dict_entries():
    """Non-dict entries inside groups[i].tasks[] must be silently dropped,
    not crash the caller."""
    result = {
        "groups": [
            {"tasks": [{"id": "ok"}, "bad-string", 42, None, {"id": "also-ok"}]},
        ],
    }
    assert [t["id"] for t in NirvanaClient._extract_tasks_from_result(result)] == ["ok", "also-ok"]


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
    """fetch() walks the real groups[].tasks[] response shape and returns a
    NormalisedTask (was silently returning [] before 2026-10-20 fix)."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={
            "ok": True,
            "result": {
                "groups": [
                    {"tasks": [
                        {"id": "other", "name": "Other", "state": "next", "starred": False},
                        {"id": "N1", "name": "Buy milk", "state": "next", "starred": True, "duedate": "2026-08-01"},
                    ]},
                ],
                "has_more": False,
            },
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
        json={
            "ok": True,
            "result": {
                "groups": [
                    {"tasks": [{"id": "other", "name": "Other"}]},
                ],
                "has_more": False,
            },
        },
    )
    client = NirvanaClient(pat="pat")
    with pytest.raises(NirvanaNotFoundError):
        await client.fetch("N-missing")
    await client.close()


@pytest.mark.asyncio
async def test_fetch_scopes_to_zoho_tag(httpx_mock):
    """2026-07-28: fetch() must filter by tags=["Zoho"]; cursor pagination is
    used if the response carries has_more=True."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={
            "ok": True,
            "result": {
                "groups": [
                    {"tasks": [{"id": "N1", "name": "Buy milk", "state": "next"}]},
                ],
                "has_more": False,
            },
        },
    )
    client = NirvanaClient(pat="pat")
    result = await client.fetch("N1")
    assert result.title == "Buy milk"
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body["tags"] == ["Zoho"]
    assert body["limit"] == NirvanaClient.FETCH_PAGE_SIZE
    # No cursor on the first request.
    assert "cursor" not in body
    await client.close()


@pytest.mark.asyncio
async def test_fetch_paginates_across_multiple_pages(httpx_mock):
    """get_tasks_paginated now uses cursor pagination, passing the previous
    response's next_cursor as the next call's `cursor` argument (fixed
    2026-10-20 after Nirvana's offset/limit semantics drifted)."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={
            "ok": True,
            "result": {
                "groups": [
                    {"tasks": [{"id": "N1", "name": "Page 1 task"}]},
                ],
                "has_more": True,
                "next_cursor": "cur-page-2",
            },
        },
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/get_tasks",
        method="POST",
        json={
            "ok": True,
            "result": {
                "groups": [
                    {"tasks": [{"id": "N2", "name": "Page 2 task"}]},
                ],
                "has_more": False,
                "next_cursor": None,
            },
        },
    )
    client = NirvanaClient(pat="pat")
    result = await client.fetch("N2")
    assert result.title == "Page 2 task"

    # Page-1 request had no cursor; page-2 request used the next_cursor.
    requests = httpx_mock.get_requests()
    body1 = json.loads(requests[0].content)
    body2 = json.loads(requests[1].content)
    assert "cursor" not in body1
    assert body2["cursor"] == "cur-page-2"
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
        return {
            "groups": [{"tasks": [{"id": f"N{call_count}"}]}],
            "has_more": True,
            "next_cursor": "loop",
        }

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
            return {
                "groups": [{"tasks": [{"id": "N1"}]}],
                "has_more": True,
                "next_cursor": "more",
            }
        # Page 2 is empty; has_more is stale-True but no tasks.
        return {"groups": [{"tasks": []}], "has_more": True, "next_cursor": "stale"}

    with patch.object(client, "call_tool", side_effect=fake_call_tool):
        tasks = await client.get_tasks_paginated(page_size=10, max_pages=25)

    assert call_count == 2
    assert len(tasks) == 1
    await client.close()


@pytest.mark.asyncio
async def test_get_tasks_paginated_breaks_on_non_dict_result():
    """Defensive: if Nirvana ever returns a body shape we don't recognise
    (e.g. a bare list at the top level) we must stop paginating rather than
    infinite-loop or crash."""
    client = NirvanaClient(pat="pat")

    async def fake_call_tool(tool, args):
        return [{"id": "unexpected-list-shape"}]

    with patch.object(client, "call_tool", side_effect=fake_call_tool):
        tasks = await client.get_tasks_paginated(page_size=10, max_pages=5)

    assert tasks == []
    await client.close()


@pytest.mark.asyncio
async def test_create_delegates_to_create_nirvana_task_and_accepts_description(complete_env):
    """description becomes the Nirvana task's note (2026-07-28 decision).
    needs complete_env because importing app.nirvana.writer imports
    app.core.config eagerly via app.core.notifications."""
    client = NirvanaClient(pat="pat")
    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    with patch("app.nirvana.writer.create_nirvana_task", new=AsyncMock(return_value="N9")) as mocked:
        result = await client.create(normalised, "Z1", description="x")
    mocked.assert_awaited_once_with(normalised, "Z1", client, note="x")
    assert result == "N9"


@pytest.mark.asyncio
async def test_update_delegates_to_update_nirvana_task(complete_env):
    client = NirvanaClient(pat="pat")
    normalised = NormalisedTask(title="t", due_date=None, priority=1, is_completed=False)
    with patch("app.nirvana.writer.update_nirvana_task", new=AsyncMock()) as mocked:
        await client.update("N1", normalised)
    mocked.assert_awaited_once_with("N1", normalised, client)


@pytest.mark.asyncio
async def test_complete_delegates_to_complete_nirvana_task(complete_env):
    client = NirvanaClient(pat="pat")
    with patch("app.nirvana.writer.complete_nirvana_task", new=AsyncMock()) as mocked:
        await client.complete("N1")
    mocked.assert_awaited_once_with("N1", client)


@pytest.mark.asyncio
async def test_delete_delegates_to_delete_nirvana_task(complete_env):
    client = NirvanaClient(pat="pat")
    with patch("app.nirvana.writer.delete_nirvana_task", new=AsyncMock()) as mocked:
        await client.delete("N1", task_name="Buy milk")
    mocked.assert_awaited_once_with("N1", client, task_name="Buy milk")
