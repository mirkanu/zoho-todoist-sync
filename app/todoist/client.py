"""Todoist HTTP client: wraps TodoistAPIAsync for REST + raw httpx for Sync API.

Typed exceptions mirror app.zoho.client convention:
- TodoistAuthError (401)   → stop sync + alert
- TodoistNotFoundError (404)
- TodoistRateLimitError (429) → retry with backoff
- TodoistAPIError (other non-2xx)
"""
from __future__ import annotations

from typing import Any

import httpx
from todoist_api_python.api_async import TodoistAPIAsync
from todoist_api_python.models import Task

from app.core.logging import get_logger

log = get_logger(__name__)

SYNC_API_URL = "https://api.todoist.com/api/v1/sync"


class TodoistAuthError(Exception):
    """Raised on 401 — API token invalid. Do NOT retry — stop and alert."""


class TodoistNotFoundError(Exception):
    """Raised on 404 — task does not exist (may be deleted)."""


class TodoistRateLimitError(Exception):
    """Raised on 429 — rate limit exceeded. Retry with backoff."""


class TodoistAPIError(Exception):
    """Raised on other non-2xx responses."""


class TodoistClient:
    """Async Todoist REST + Sync API client."""

    def __init__(self, api_token: str) -> None:
        self._api_token = api_token  # stored for Sync API Authorization header
        self._api = TodoistAPIAsync(token=api_token)

    async def close(self) -> None:
        await self._api.close()

    async def fetch_todoist_task(self, todoist_task_id: str) -> Task:
        """Fetch a single Todoist task. Raises typed exceptions on non-2xx."""
        try:
            task = await self._api.get_task(todoist_task_id)
        except httpx.HTTPStatusError as exc:
            self._raise_typed(exc.response.status_code, f"GET task {todoist_task_id}", exc)
        log.info("todoist_fetch_task", todoist_id=todoist_task_id)
        return task

    @staticmethod
    def _raise_typed(status: int, context: str, cause: Exception) -> None:
        if status == 401:
            raise TodoistAuthError(f"401 Unauthorized — {context}") from cause
        if status == 404:
            raise TodoistNotFoundError(f"404 Not Found — {context}") from cause
        if status == 429:
            raise TodoistRateLimitError(f"429 Rate limit — {context}") from cause
        raise TodoistAPIError(f"{status} — {context}") from cause

    async def fetch_sync_delta(
        self,
        sync_token: str,
        project_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Call the Todoist Sync API for incremental item updates.

        Returns (items, new_sync_token). When project_id is provided, items
        are filtered client-side (the Sync API has no server-side project
        filter — see RESEARCH.md Pitfall 1).

        Raises TodoistAuthError (401), TodoistRateLimitError (429),
        TodoistAPIError (other non-2xx).
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SYNC_API_URL,
                headers={"Authorization": f"Bearer {self._api_token}"},
                data={
                    "sync_token": sync_token,
                    "resource_types": '["items"]',
                },
            )
        if resp.status_code == 401:
            raise TodoistAuthError("401 Unauthorized — Sync API")
        if resp.status_code == 429:
            raise TodoistRateLimitError("429 Rate limit — Sync API")
        if not (200 <= resp.status_code < 300):
            raise TodoistAPIError(f"{resp.status_code} — Sync API: {resp.text[:200]}")
        body = resp.json()
        items: list[dict[str, Any]] = body.get("items", []) or []
        new_token: str = body["sync_token"]
        if project_id is not None:
            items = [i for i in items if i.get("project_id") == project_id]
        log.info(
            "todoist_sync_delta",
            item_count=len(items),
            project_filtered=project_id is not None,
        )
        return items, new_token
