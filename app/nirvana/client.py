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

    @staticmethod
    def _extract_tasks_from_result(result: Any) -> list[dict[str, Any]]:
        """Parse a get_tasks tool result into a flat list of task dicts.

        Real Nirvana API response (confirmed live 2026-10-20) wraps the task
        list inside result.groups[].tasks[]; top-level keys are also
        present (ok, grammar_version, total, returned, has_more, next_cursor,
        groups, hints, ordering). Earlier code parsed result.tasks or treated
        the body as a flat list — both shapes were wrong and silently returned
        an empty list, which is how orphan_sweep reported every Nirvana task
        as missing on 2026-10-20 (76 false-positive Zoho deletions). See
        memory `nirvana-client-broken` for the full timeline. We do NOT
        support the older result.tasks / bare-list shapes anywhere now that
        that bug shipped.
        """
        if not isinstance(result, dict):
            return []
        groups = result.get("groups") or []
        if not isinstance(groups, list):
            return []
        tasks: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            grp_tasks = group.get("tasks") or []
            if isinstance(grp_tasks, list):
                tasks.extend(t for t in grp_tasks if isinstance(t, dict))
        return tasks

    async def get_tasks(self, **filters: Any) -> list[dict[str, Any]]:
        result = await self.call_tool("get_tasks", filters)
        return self._extract_tasks_from_result(result)

    async def get_tasks_paginated(
        self,
        page_size: int = FETCH_PAGE_SIZE,
        max_pages: int = MAX_FETCH_PAGES,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """Loop get_tasks using cursor-based pagination until exhausted or
        the max_pages safety cap is hit.

        The API exposes `has_more` (bool) and `next_cursor` (string or null)
        at the top level of the tool result; passing the previous
        `next_cursor` as `cursor` in the next call returns the following
        page. The previous (broken) implementation used offset/limit and
        looked for `has_more` inside `result.tasks` — that returned the
        right page-1 sometimes but never page 2+, and falsely reported
        `has_more=False` once Nirvana's default ordering put a real task
        outside the first 200-item page. Real bug, real incident
        ([[nirvana-client-broken]], [[2026-10-20-orphan-sweep-incident]]).
        `page_size` is still passed as `limit` on every call; that's the
        only carry-over from the old shape that the API honours unchanged.
        """
        all_tasks: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            args: dict[str, Any] = {**filters, "limit": page_size}
            if cursor:
                args["cursor"] = cursor
            result = await self.call_tool("get_tasks", args)
            if not isinstance(result, dict):
                # Unexpected shape — defensive break rather than infinite loop.
                break
            tasks = self._extract_tasks_from_result(result)
            all_tasks.extend(tasks)
            has_more = bool(result.get("has_more"))
            next_cursor = result.get("next_cursor")
            # Stop on the canonical "no more pages" signal, OR on a defensive
            # safety net: an empty page while has_more/next_cursor still
            # claim there's more is treated as end-of-stream rather than an
            # infinite loop. Real cases (2026-07-28 sweeps) saw Nirvana's
            # backend occasionally reply with has_more=True + 0 items on the
            # very last page; honouring the safety net kept the old offset
            # version from chasing ghosts.
            if not has_more or not next_cursor or not tasks:
                break
            cursor = next_cursor
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
        """Fetch one task by id. Nirvana's get_tasks has no single-id filter
        (only state/tags/query/starred/overdue/due_before per D-04).

        Scoped to tags=["Zoho"] rather than scanning the whole account:
        confirmed live 2026-07-28 that every task this sync creates carries
        the "Zoho" tag (app.nirvana.writer.BASE_TAGS), so this filter
        reliably narrows the scan to only sync-managed tasks — dramatically
        shrinking scan scope and sidestepping the older
        silent-NotFound-after-page-1 bug that hit production on 2026-10-20
        when the previous offset/has_more parsing fell off the real
        task list (see [[nirvana-client-broken]]).
        Cursor-paginates via get_tasks_paginated as a defensive fallback
        should Zoho-tagged tasks themselves ever exceed one page."""
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
