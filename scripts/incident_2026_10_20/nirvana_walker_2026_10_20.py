"""Recover titles/due_dates for the 76 Nirvana tasks deleted today.

Standalone walker that does NOT use the broken NirvanaClient (its response
parsing and offset/limit pagination are stale — see worker/reconciler.py
incident 2026-10-20). Uses cursor-based pagination directly against the
NIRVANA_BASE_URL MCP endpoint and walks `groups[].tasks[]`.

Prints CSV of (zoho_task_id, nirvana_id, title, due_date) on stdout.
"""
from __future__ import annotations

import asyncio
import csv
import io
import sys
from typing import Any

import httpx

from app.core.config import get_settings

NIRVANA_BASE_URL = "https://mcp.nirvanahq.com/playground/run"


async def fetch_all_tasks(
    client: httpx.AsyncClient, pat: str, page_size: int = 200
) -> list[dict[str, Any]]:
    """Walk all tasks via cursor pagination, walking groups[].tasks[]."""
    cursor: str | None = None
    out: list[dict[str, Any]] = []
    pages = 0
    while True:
        args: dict[str, Any] = {"limit": page_size}
        if cursor:
            args["cursor"] = cursor
        resp = await client.post(
            f"{NIRVANA_BASE_URL}/get_tasks",
            headers={"Authorization": f"Bearer {pat}"},
            json=args,
        )
        if resp.status_code != 200:
            print(f"ERR status={resp.status_code} body={resp.text[:200]}", file=sys.stderr)
            break
        body = resp.json()
        # call_tool shape: body = {"ok": true, "result": {...}}. Direct REST: body = {"ok": true, ...}
        result = body.get("result", body)
        if not isinstance(result, dict) or not result.get("ok", True):
            print(f"ERR ok=false body={str(body)[:300]}", file=sys.stderr)
            break
        for group in result.get("groups", []):
            for task in group.get("tasks", []):
                out.append(task)
        cursor = result.get("next_cursor")
        pages += 1
        if not cursor:
            break
        if pages > 50:  # 10000 tasks max safety
            print("Reached 50 pages, stopping.", file=sys.stderr)
            break
    print(f"Walked {pages} pages, collected {len(out)} tasks total", file=sys.stderr)
    return out


async def main() -> None:
    s = get_settings()

    # Read 76 target pairs from sync_events.
    import asyncpg  # noqa: F401
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(s.database_url, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as session:
        result = await session.execute(
            text(
                """
                SELECT DISTINCT ON (detail->>'external_task_id')
                       zoho_task_id,
                       detail->>'external_task_id' AS nirvana_id
                FROM sync_events
                WHERE action = 'orphan'
                  AND created_at >= '2026-10-20 10:00:00'
                ORDER BY detail->>'external_task_id', created_at DESC
                """
            )
        )
        pairs = [(r[0], r[1]) for r in result.fetchall() if r[1]]
    await engine.dispose()

    # Walk Nirvana with the corrected cursor-based pagination.
    async with httpx.AsyncClient(timeout=60) as client:
        all_tasks = await fetch_all_tasks(client, s.nirvana_pat)

    by_id = {t.get("id"): t for t in all_tasks}
    print(f"Looked up {len(by_id)} unique Nirvana IDs in {len(all_tasks)} tasks", file=sys.stderr)

    # Emit CSV for the 76 pairs.
    writer = csv.writer(sys.stdout)
    writer.writerow(["old_zoho_id", "nirvana_id", "found", "title", "due_date", "tags"])
    found_count = 0
    missing_count = 0
    for zid, nid in pairs:
        task = by_id.get(nid)
        if task:
            found_count += 1
            writer.writerow([
                zid, nid, "1",
                task.get("name", ""),
                task.get("duedate", ""),
                "|".join(task.get("tags", []) or []),
            ])
        else:
            missing_count += 1
            writer.writerow([zid, nid, "0", "", "", ""])
    print(f"Found {found_count}, missing {missing_count}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
