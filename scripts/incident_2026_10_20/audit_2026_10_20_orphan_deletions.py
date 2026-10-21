"""Audit the 76 Nirvana tasks deleted today.

Produces a single CSV with:
  - old_zoho_id
  - nirvana_id
  - state (active / trash / inactive / not_found)
  - title
  - due_date
  - tags
  - created_at_iso (from Nirvana if available)
  - modified_at_iso (from Nirvana if available)
  - deleted_from_sync_state_at (the time orphan_sweep deleted the Zoho
    counterpart — from sync_events)

Designed to be one-shot. Prints CSV to stdout, summary to stderr.
"""
from __future__ import annotations

import asyncio
import csv
import json
import sys
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.nirvana.client import NirvanaClient  # just to call out we are NOT using its broken fetch; we use raw REST

NIRVANA_BASE_URL = "https://mcp.nirvanahq.com/playground/run"


async def walk_nirvana(
    client: httpx.AsyncClient, pat: str, query: dict | None = None
) -> list[dict[str, Any]]:
    """Cursor-paginated walk. Returns concatenated tasks across all groups."""
    cursor: str | None = None
    out: list[dict[str, Any]] = []
    pages = 0
    while True:
        args: dict[str, Any] = {"limit": 500}
        if cursor:
            args["cursor"] = cursor
        if query:
            args.update(query)
        resp = await client.post(
            f"{NIRVANA_BASE_URL}/get_tasks",
            headers={"Authorization": f"Bearer {pat}"},
            json=args,
        )
        if resp.status_code != 200:
            print(f"ERR status={resp.status_code} body={resp.text[:200]}", file=sys.stderr)
            break
        body = resp.json()
        result = body.get("result", body)
        if not (result.get("ok", True) if isinstance(result, dict) else True):
            print(f"ERR ok=false body={str(body)[:300]}", file=sys.stderr)
            break
        for group in result.get("groups", []):
            for task in group.get("tasks", []):
                out.append(task)
        cursor = result.get("next_cursor") if isinstance(result, dict) else None
        pages += 1
        if not cursor or pages > 50:
            break
    return out


async def main() -> None:
    s = get_settings()
    engine = create_async_engine(s.database_url, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    # Pull the 76 (zoho_id, nirvana_id) pairs + their sync_events deletion time.
    async with sf() as session:
        result = await session.execute(
            text(
                """
                SELECT DISTINCT ON (detail->>'external_task_id')
                       zoho_task_id,
                       detail->>'external_task_id' AS nirvana_id,
                       created_at AS deleted_at
                FROM sync_events
                WHERE action = 'orphan'
                  AND created_at >= '2026-10-20 10:00:00'
                ORDER BY detail->>'external_task_id', created_at DESC
                """
            )
        )
        pairs = [(r[0], r[1], r[2].isoformat() if r[2] else "") for r in result.fetchall() if r[1]]
    await engine.dispose()
    print(f"Loaded {len(pairs)} pairs from sync_events", file=sys.stderr)

    # Walk Nirvana (active, trash, inactive) and bucket by id.
    buckets: dict[str, str] = {}
    payload: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=60) as http:
        for label, q in [
            ("active", None),
            ("trash", {"query": "in:trash"}),
            ("inactive", {"query": "is:inactive"}),
        ]:
            tasks = await walk_nirvana(http, s.nirvana_pat, q)
            for t in tasks:
                tid = str(t.get("id", ""))
                if tid in buckets:
                    continue
                buckets[tid] = label
                payload[tid] = t
            print(f"walked {label}: {len(tasks)} tasks", file=sys.stderr)

    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "old_zoho_id",
            "nirvana_id",
            "state",
            "found",
            "title",
            "due_date",
            "tags",
            "created_at",
            "modified_at",
            "deleted_zoho_at",
        ]
    )
    found_active = found_trash = found_inactive = not_found = 0
    for zid, nid, deleted_at in pairs:
        label = buckets.get(nid, "not_found")
        task = payload.get(nid, {})
        title = task.get("name", "")
        due = task.get("duedate", "")
        tags = "|".join(task.get("tags") or [])
        created = task.get("createdat", "") or task.get("created_at", "")
        modified = task.get("modifiedat", "") or task.get("modified_at", "")
        if label == "active":
            found_active += 1
        elif label == "trash":
            found_trash += 1
        elif label == "inactive":
            found_inactive += 1
        else:
            not_found += 1
        writer.writerow(
            [
                zid,
                nid,
                label,
                "1" if label != "not_found" else "0",
                title,
                due,
                tags,
                created,
                modified,
                deleted_at,
            ]
        )
    print(
        f"SUMMARY: active={found_active} trash={found_trash} inactive={found_inactive} not_found={not_found}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    asyncio.run(main())
