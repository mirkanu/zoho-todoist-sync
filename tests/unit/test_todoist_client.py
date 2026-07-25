from unittest.mock import AsyncMock, patch

import pytest

from app.core.normalise import NormalisedTask
from app.todoist.client import (
    TodoistClient,
    TodoistAuthError,
    TodoistNotFoundError,
    TodoistRateLimitError,
    TodoistAPIError,
)

TODOIST_TASK_URL_PREFIX = "https://api.todoist.com/api/v1/tasks/"


@pytest.mark.asyncio
async def test_fetch_task_success(httpx_mock):
    httpx_mock.add_response(
        url=f"{TODOIST_TASK_URL_PREFIX}123",
        json={
            "id": "123",
            "content": "Buy milk",
            "description": "",
            "priority": 3,
            "due": None,
            "labels": [],
            "project_id": "6gCPcWwM392GhXQh",
            "is_completed": False,
            "completed_at": None,
            "added_at": "2026-04-01T00:00:00Z",
            "user_id": "u1",
            "creator_id": "u1",
            "assignee_id": None,
            "assigner_id": None,
            "comment_count": 0,
            "order": 0,
            "parent_id": None,
            "section_id": None,
            "duration": None,
            "url": "https://todoist.com/showTask?id=123",
            "day_order": -1,
            "is_collapsed": False,
            "deadline": None,
            "updated_at": "2026-04-01T00:00:00Z",
        },
    )
    client = TodoistClient(api_token="test-token")
    task = await client.fetch_todoist_task("123")
    assert task.id == "123"
    assert task.content == "Buy milk"
    assert task.priority == 3
    await client.close()


@pytest.mark.asyncio
async def test_fetch_task_401_raises_auth_error(httpx_mock):
    httpx_mock.add_response(url=f"{TODOIST_TASK_URL_PREFIX}123", status_code=401, json={})
    client = TodoistClient(api_token="bad")
    with pytest.raises(TodoistAuthError):
        await client.fetch_todoist_task("123")
    await client.close()


@pytest.mark.asyncio
async def test_fetch_task_404_raises_not_found(httpx_mock):
    httpx_mock.add_response(url=f"{TODOIST_TASK_URL_PREFIX}999", status_code=404, json={})
    client = TodoistClient(api_token="tok")
    with pytest.raises(TodoistNotFoundError):
        await client.fetch_todoist_task("999")
    await client.close()


@pytest.mark.asyncio
async def test_fetch_task_429_raises_rate_limit(httpx_mock):
    httpx_mock.add_response(url=f"{TODOIST_TASK_URL_PREFIX}123", status_code=429, json={})
    client = TodoistClient(api_token="tok")
    with pytest.raises(TodoistRateLimitError):
        await client.fetch_todoist_task("123")
    await client.close()


@pytest.mark.asyncio
async def test_fetch_task_500_raises_api_error(httpx_mock):
    httpx_mock.add_response(url=f"{TODOIST_TASK_URL_PREFIX}123", status_code=500, json={})
    client = TodoistClient(api_token="tok")
    with pytest.raises(TodoistAPIError):
        await client.fetch_todoist_task("123")
    await client.close()


SYNC_URL = "https://api.todoist.com/api/v1/sync"


@pytest.mark.asyncio
async def test_sync_delta_full_sync_wildcard(httpx_mock):
    httpx_mock.add_response(
        url=SYNC_URL,
        method="POST",
        json={"items": [], "sync_token": "new-token-1", "full_sync": True},
    )
    client = TodoistClient(api_token="tok")
    items, new_token = await client.fetch_sync_delta(sync_token="*")
    assert items == []
    assert new_token == "new-token-1"
    # confirm request body contained sync_token=*
    req = httpx_mock.get_request()
    assert b"sync_token=%2A" in req.content or b"sync_token=*" in req.content
    await client.close()


@pytest.mark.asyncio
async def test_sync_delta_filters_by_project_id(httpx_mock):
    httpx_mock.add_response(
        url=SYNC_URL,
        method="POST",
        json={
            "items": [
                {"id": "1", "content": "Mine", "project_id": "6gCPcWwM392GhXQh", "description": "[zoho:1]"},
                {"id": "2", "content": "Other", "project_id": "other-project-id", "description": ""},
                {"id": "3", "content": "Also mine", "project_id": "6gCPcWwM392GhXQh", "description": ""},
            ],
            "sync_token": "new-token-2",
        },
    )
    client = TodoistClient(api_token="tok")
    items, new_token = await client.fetch_sync_delta(
        sync_token="prev-token", project_id="6gCPcWwM392GhXQh"
    )
    assert len(items) == 2
    assert {i["id"] for i in items} == {"1", "3"}
    assert new_token == "new-token-2"
    await client.close()


@pytest.mark.asyncio
async def test_sync_delta_no_project_filter_returns_all(httpx_mock):
    httpx_mock.add_response(
        url=SYNC_URL,
        method="POST",
        json={
            "items": [
                {"id": "1", "project_id": "a"},
                {"id": "2", "project_id": "b"},
            ],
            "sync_token": "tok2",
        },
    )
    client = TodoistClient(api_token="tok")
    items, new_token = await client.fetch_sync_delta(sync_token="prev")
    assert len(items) == 2
    await client.close()


@pytest.mark.asyncio
async def test_sync_delta_401_raises_auth_error(httpx_mock):
    httpx_mock.add_response(url=SYNC_URL, method="POST", status_code=401, json={})
    client = TodoistClient(api_token="bad")
    with pytest.raises(TodoistAuthError):
        await client.fetch_sync_delta(sync_token="*")
    await client.close()


@pytest.mark.asyncio
async def test_sync_delta_429_raises_rate_limit(httpx_mock):
    httpx_mock.add_response(url=SYNC_URL, method="POST", status_code=429, json={})
    client = TodoistClient(api_token="tok")
    with pytest.raises(TodoistRateLimitError):
        await client.fetch_sync_delta(sync_token="*")
    await client.close()


@pytest.mark.asyncio
async def test_sync_delta_500_raises_api_error(httpx_mock):
    httpx_mock.add_response(url=SYNC_URL, method="POST", status_code=500, json={})
    client = TodoistClient(api_token="tok")
    with pytest.raises(TodoistAPIError):
        await client.fetch_sync_delta(sync_token="*")
    await client.close()


@pytest.mark.asyncio
async def test_sync_delta_sends_bearer_auth(httpx_mock):
    httpx_mock.add_response(
        url=SYNC_URL, method="POST",
        json={"items": [], "sync_token": "t"},
    )
    client = TodoistClient(api_token="secret-token-value")
    await client.fetch_sync_delta(sync_token="*")
    req = httpx_mock.get_request()
    assert req.headers["Authorization"] == "Bearer secret-token-value"
    await client.close()


# ---- TaskProvider protocol conformance (Plan 09-04) ----

SOME_NORMALISED = NormalisedTask(
    title="Buy milk", due_date="2026-07-25", priority=3, is_completed=False
)


@pytest.mark.asyncio
async def test_provider_fetch_delegates_to_fetch_todoist_task_and_normalises():
    client = TodoistClient(api_token="tok")
    fake_task = object()
    with (
        patch.object(client, "fetch_todoist_task", new=AsyncMock(return_value=fake_task)) as mock_fetch,
        patch("app.todoist.normalise.todoist_task_to_normalised", return_value=SOME_NORMALISED) as mock_norm,
    ):
        result = await client.fetch("123")
    mock_fetch.assert_awaited_once_with("123")
    mock_norm.assert_called_once_with(fake_task)
    assert result == SOME_NORMALISED


@pytest.mark.asyncio
async def test_provider_create_without_description_delegates():
    client = TodoistClient(api_token="tok")
    with patch(
        "app.todoist.writer.create_todoist_task", new=AsyncMock(return_value="T1")
    ) as mock_create:
        result = await client.create(SOME_NORMALISED, "Z1")
    mock_create.assert_awaited_once_with(SOME_NORMALISED, "Z1", client._api, description=None)
    assert result == "T1"


@pytest.mark.asyncio
async def test_provider_create_with_description_preserves_desc_1_4_behavior():
    client = TodoistClient(api_token="tok")
    with patch(
        "app.todoist.writer.create_todoist_task", new=AsyncMock(return_value="T2")
    ) as mock_create:
        result = await client.create(SOME_NORMALISED, "Z1", description="<p>context</p>")
    mock_create.assert_awaited_once_with(
        SOME_NORMALISED, "Z1", client._api, description="<p>context</p>"
    )
    assert result == "T2"


@pytest.mark.asyncio
async def test_provider_update_delegates():
    client = TodoistClient(api_token="tok")
    with patch(
        "app.todoist.writer.update_todoist_task", new=AsyncMock(return_value=None)
    ) as mock_update:
        await client.update("T1", SOME_NORMALISED)
    mock_update.assert_awaited_once_with("T1", SOME_NORMALISED, client._api)


@pytest.mark.asyncio
async def test_provider_complete_delegates():
    client = TodoistClient(api_token="tok")
    with patch(
        "app.todoist.writer.complete_todoist_task", new=AsyncMock(return_value=None)
    ) as mock_complete:
        await client.complete("T1")
    mock_complete.assert_awaited_once_with("T1", client._api)


@pytest.mark.asyncio
async def test_provider_delete_delegates():
    client = TodoistClient(api_token="tok")
    with patch(
        "app.todoist.writer.delete_todoist_task", new=AsyncMock(return_value=None)
    ) as mock_delete:
        await client.delete("T1", task_name="X")
    mock_delete.assert_awaited_once_with("T1", client._api, task_name="X")
