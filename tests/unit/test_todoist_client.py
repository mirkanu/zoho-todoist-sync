import pytest

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
