"""Nirvana HTTP client: httpx REST wrapper for the MCP REST fallback endpoint.

Typed exceptions mirror app.todoist.client convention:
- NirvanaAuthError (401)   -> stop sync + alert
- NirvanaNotFoundError (404)
- NirvanaRateLimitError (429) -> retry with backoff
- NirvanaAPIError (other non-2xx, or ok=false tool-level failure)
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

NIRVANA_BASE_URL = "https://mcp.nirvanahq.com/playground/run"


class NirvanaAuthError(Exception):
    """Raised on 401 — PAT invalid. Do NOT retry — stop and alert."""


class NirvanaNotFoundError(Exception):
    """Raised on 404 — task does not exist (may be deleted/trashed)."""


class NirvanaRateLimitError(Exception):
    """Raised on 429 — rate limit exceeded. Retry with backoff."""


class NirvanaAPIError(Exception):
    """Raised on other non-2xx responses, or ok=false in a 2xx tool response."""


class NirvanaClient:
    """Async client for Nirvana's MCP REST wrapper (D-02: plain httpx, no MCP SDK)."""

    FETCH_PAGE_SIZE = 200
    MAX_FETCH_PAGES = 25  # safety cap: 5000 Zoho-tagged tasks, defensive only

    def __init__(self, pat: str) -> None:
        self._pat = pat
        self._http = httpx.AsyncClient(timeout=15)

    async def close(self) -> None:
        await self._http.aclose()

    async def call_tool(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = await self._http.post(
            f"{NIRVANA_BASE_URL}/{tool}",
            headers={"Authorization": f"Bearer {self._pat}"},
            json=args or {},
        )
        if resp.status_code == 401:
            raise NirvanaAuthError(f"401 Unauthorized — tool {tool}")
        if resp.status_code == 404:
            raise NirvanaNotFoundError(f"404 Not Found — tool {tool}")
        if resp.status_code == 429:
            raise NirvanaRateLimitError(f"429 Rate limit — tool {tool}")
        if not (200 <= resp.status_code < 300):
            raise NirvanaAPIError(f"{resp.status_code} — tool {tool}: {resp.text[:200]}")
        body = resp.json()
        if not body.get("ok", False):
            raise NirvanaAPIError(f"tool {tool} returned ok=false: {body}")
        log.info("nirvana_tool_call", tool=tool)
        result = body.get("result")
        return result if result is not None else {}

    async def get_tasks(self, **filters: Any) -> list[dict[str, Any]]:
        result = await self.call_tool("get_tasks", filters)
        if isinstance(result, list):
            return result
        return result.get("tasks", []) if isinstance(result, dict) else []

    async def get_tasks_paginated(
        self,
        page_size: int = FETCH_PAGE_SIZE,
        max_pages: int = MAX_FETCH_PAGES,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """Loop get_tasks via offset/has_more until exhausted or max_pages
        safety cap is hit (confirmed live 2026-07-28: offset/has_more are
        real, working pagination fields — get_tasks is NOT hard-capped at a
        single 200-item page as originally assumed; that assumption produced
        a real production bug, see fetch()'s docstring). Returns the
        concatenated task list across all pages."""
        all_tasks: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            result = await self.call_tool(
                "get_tasks", {**filters, "limit": page_size, "offset": offset}
            )
            tasks = result.get("tasks", []) if isinstance(result, dict) else (
                result if isinstance(result, list) else []
            )
            all_tasks.extend(tasks)
            has_more = bool(result.get("has_more")) if isinstance(result, dict) else False
            if not has_more or not tasks:
                break
            offset += page_size
        return all_tasks

    async def get_tags(self) -> Any:
        return await self.call_tool("get_tags", {})

    async def get_task_counts(self) -> dict[str, int]:
        return await self.call_tool("get_task_counts", {})

    async def create_tasks(self, items: list[dict[str, Any]]) -> Any:
        return await self.call_tool("create_tasks", {"tasks": items})

    async def update_tasks(self, updates: list[dict[str, Any]]) -> Any:
        return await self.call_tool("update_tasks", {"updates": updates})

    # ---- TaskProvider protocol conformance (see app/providers/base.py, Plan 04) ----

    async def fetch(self, external_id: str) -> Any:
        """Fetch one task by id. Nirvana's get_tasks has no single-id filter (only
        state/tags/query/starred/overdue/due_before per D-04).

        Scoped to tags=["Zoho"] rather than scanning the whole account:
        confirmed live 2026-07-28 that every task this sync creates carries
        the "Zoho" tag (app.nirvana.writer.BASE_TAGS), so this filter reliably
        narrows the scan to only sync-managed tasks (43 vs 551 total tasks in
        this account at time of writing) — dramatically shrinking scan scope
        and sidestepping the 200-item single-page cap that previously caused
        false NotFoundErrors in production once the account grew past 200
        total tasks (original Pitfall 4 mitigation was an unfiltered single
        page; that silently missed tasks outside the first 200 by whatever
        the API's default ordering is — confirmed as a real, not hypothetical,
        production bug). Still paginates via get_tasks_paginated as a
        defensive fallback should Zoho-tagged tasks themselves ever exceed
        one page."""
        from app.nirvana.normalise import nirvana_task_to_normalised

        tasks = await self.get_tasks_paginated(tags=["Zoho"])
        for task in tasks:
            if str(task.get("id")) == str(external_id):
                return nirvana_task_to_normalised(task)
        raise NirvanaNotFoundError(
            f"404 Not Found — task {external_id} not found among Zoho-tagged tasks"
        )

    async def create(self, normalised: Any, zoho_task_id: str, description: str | None = None) -> str:
        # description becomes the Nirvana task's `note` field (2026-07-28
        # decision) — built by app.nirvana.description.build_task_note and
        # passed through here so this method's signature still matches
        # TodoistClient.create()'s, satisfying the shared TaskProvider Protocol.
        from app.nirvana.writer import create_nirvana_task

        return await create_nirvana_task(normalised, zoho_task_id, self, note=description)

    async def update(self, external_id: str, normalised: Any) -> None:
        from app.nirvana.writer import update_nirvana_task

        await update_nirvana_task(external_id, normalised, self)

    async def complete(self, external_id: str) -> None:
        from app.nirvana.writer import complete_nirvana_task

        await complete_nirvana_task(external_id, self)

    async def delete(self, external_id: str, task_name: str | None = None) -> None:
        from app.nirvana.writer import delete_nirvana_task

        await delete_nirvana_task(external_id, self, task_name=task_name)
