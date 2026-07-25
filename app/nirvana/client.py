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
        return body.get("result") or {}

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
