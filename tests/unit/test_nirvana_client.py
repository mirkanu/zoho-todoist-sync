import json

import pytest

from app.nirvana.client import (
    NirvanaAPIError,
    NirvanaAuthError,
    NirvanaClient,
    NirvanaNotFoundError,
    NirvanaRateLimitError,
)

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
