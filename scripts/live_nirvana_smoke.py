"""One-shot live-network smoke test for the 2026-10-20 NirvanaClient fix.

Owner runs this manually once after the worker image rebuild. Hits the real
Nirvana API with the configured PAT, walks `groups[].tasks[]` via cursor
pagination, and asserts at least one task is returned AND a known id from
sync_state can be fetched. Also exercises `NirvanaClient.fetch()` to confirm
the per-id lookup path works end-to-end.

Exits 0 on success, 1 on any failure. NOT a pytest test (deliberately inline)
because the harness doesn't have the live PAT.

Usage (from project root):

  cd /home/services/hetzner-vps && \
    docker compose run --rm --no-deps -e PYTHONPATH=/app zoho-sync-worker \
    python /work/live_nirvana_smoke.py
"""
from __future__ import annotations

import asyncio
import sys

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import SyncState
from app.nirvana.client import NirvanaClient

log = get_logger(__name__)


async def main() -> int:
    s = get_settings()
    configure_logging(s.log_level)
    if not s.nirvana_pat:
        print("ERROR: NIRVANA_PAT not set in env", file=sys.stderr)
        return 1

    # 1. Raw walker — exercises the wire format directly (groups[].tasks + cursor).
    async with httpx.AsyncClient(timeout=60) as http:
        cursor = None
        pages = 0
        all_tasks: list[dict] = []
        while pages < 50:
            args: dict = {"limit": 500}
            if cursor:
                args["cursor"] = cursor
            r = await http.post(
                "https://mcp.nirvanahq.com/playground/run/get_tasks",
                headers={"Authorization": f"Bearer {s.nirvana_pat}"},
                json=args,
            )
            r.raise_for_status()
            body = r.json()
            result = body.get("result", body)
            for group in result.get("groups", []):
                all_tasks.extend(group.get("tasks", []))
            cursor = result.get("next_cursor")
            pages += 1
            if not cursor:
                break
    print(f"walker: {len(all_tasks)} tasks across {pages} pages")
    if not all_tasks:
        print("ERROR: walker returned 0 tasks — parser or wire shape is wrong", file=sys.stderr)
        return 1

    # 2. NirvanaClient.fetch() end-to-end (the path the orphan sweep uses).
    engine = create_async_engine(s.database_url, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as session:
        result = await session.execute(
            select(SyncState.external_task_id).where(
                SyncState.provider == "nirvana"
            ).limit(3)
        )
        sample_ids = [row[0] for row in result.all()]
    await engine.dispose()

    if not sample_ids:
        print("WARN: no provider='nirvana' rows in sync_state; skipping fetch tests", file=sys.stderr)
        return 0

    client = NirvanaClient(pat=s.nirvana_pat)
    try:
        failures = 0
        for nid in sample_ids:
            try:
                task = await client.fetch(nid)
                print(f"fetch({nid[:20]}...): OK  -> {task.title[:60]!r}")
            except Exception as exc:
                print(f"fetch({nid[:20]}...): FAIL  -> {exc}", file=sys.stderr)
                failures += 1
        if failures:
            print(f"ERROR: {failures}/{len(sample_ids)} fetches failed", file=sys.stderr)
            return 1
        print(f"OK: all {len(sample_ids)} fetches resolved")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
