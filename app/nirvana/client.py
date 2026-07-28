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
        state/tags/query/starred/overdue/due_before per D-04) — scans up to 200
        results and matches by id. Acceptable at personal-account scale (D-09);
        documented limitation if the account exceeds 200 active items, see
        RESEARCH.md Pitfall 4."""
        from app.nirvana.normalise import nirvana_task_to_normalised

        tasks = await self.get_tasks(limit=200)
        for task in tasks:
            if str(task.get("id")) == str(external_id):
                return nirvana_task_to_normalised(task)
        raise NirvanaNotFoundError(f"404 Not Found — task {external_id} not in get_tasks() scan")

    async def create(self, normalised: Any, zoho_task_id: str, description: str | None = None) -> str:
        # description intentionally ignored: Nirvana has no description field,
        # and description sync stays out of scope for Nirvana per CONTEXT.md.
        # Parameter exists only so this method's signature matches
        # TodoistClient.create()'s, satisfying the shared TaskProvider Protocol.
        from app.nirvana.writer import create_nirvana_task

        return await create_nirvana_task(normalised, zoho_task_id, self)

    async def update(self, external_id: str, normalised: Any) -> None:
        from app.nirvana.writer import update_nirvana_task

        await update_nirvana_task(external_id, normalised, self)

    async def complete(self, external_id: str) -> None:
        from app.nirvana.writer import complete_nirvana_task

        await complete_nirvana_task(external_id, self)

    async def delete(self, external_id: str, task_name: str | None = None) -> None:
        from app.nirvana.writer import delete_nirvana_task

        await delete_nirvana_task(external_id, self, task_name=task_name)
