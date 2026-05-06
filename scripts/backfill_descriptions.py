"""One-shot backfill: add Phase 09 descriptions to existing synced Todoist tasks.

Usage:
    python scripts/backfill_descriptions.py [--dry-run]

Idempotent: skips tasks whose Todoist description already contains the Zoho task URL.
Tasks missing from Zoho API (deleted/reassigned) are logged and skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from todoist_api_python.api_async import TodoistAPIAsync

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import SyncState
from app.todoist.description import ZOHO_TASK_BASE_URL, build_task_description, _extract_related_to_name
from app.zoho.client import ZohoClient, ZohoNotFoundError
from app.zoho.state import token_state
from app.zoho.token_manager import (
    KV_ACCESS_TOKEN_KEY,
    KV_EXPIRES_AT_KEY,
    load_token_from_kv,
    refresh_access_token,
    upsert_kv,
)

log = get_logger(__name__)


async def backfill_one(
    zoho_task_id: str,
    todoist_task_id: str,
    zoho_client: ZohoClient,
    todoist_api: TodoistAPIAsync,
    dry_run: bool,
    counters: dict[str, int],
) -> None:
    """Backfill the description for a single synced task pair.

    Idempotent: skips if Todoist description already contains the Zoho task URL.
    Handles ZohoNotFoundError gracefully — logs and increments not_found counter.
    """
    # 1. Fetch Todoist task — check idempotency
    task = await todoist_api.get_task(todoist_task_id)
    if task.description and (ZOHO_TASK_BASE_URL + "/" + zoho_task_id) in task.description:
        log.info("backfill_skip_already_has_link", zoho_id=zoho_task_id, todoist_id=todoist_task_id)
        counters["skipped"] += 1
        return

    # 2. Fetch Zoho task for related_to context
    try:
        body = await zoho_client.get_task(zoho_task_id)
        zoho_record = body["data"][0]
    except ZohoNotFoundError:
        log.warning("backfill_zoho_not_found", zoho_id=zoho_task_id, todoist_id=todoist_task_id)
        counters["not_found"] += 1
        return

    # 3. Build description
    related_to_name = _extract_related_to_name(zoho_record)
    desc = build_task_description(zoho_task_id, related_to_name)

    # 4. Write to Todoist (unless dry-run)
    if not dry_run:
        await todoist_api.update_task(todoist_task_id, description=desc)
    log.info("backfill_updated", zoho_id=zoho_task_id, todoist_id=todoist_task_id, dry_run=dry_run)
    counters["updated"] += 1


async def run_backfill(
    session_factory,
    zoho_client: ZohoClient,
    todoist_api: TodoistAPIAsync,
    dry_run: bool,
) -> dict[str, int]:
    """Iterate all sync_state rows and backfill descriptions onto Todoist tasks."""
    counters = {"skipped": 0, "updated": 0, "not_found": 0, "errors": 0}

    async with session_factory() as session:
        result = await session.execute(select(SyncState))
        rows = result.scalars().all()

    log.info("backfill_start", total=len(rows), dry_run=dry_run)

    for row in rows:
        try:
            await backfill_one(row.zoho_task_id, row.todoist_task_id, zoho_client, todoist_api, dry_run, counters)
        except Exception as exc:
            log.error("backfill_error", zoho_id=row.zoho_task_id, error=str(exc), exc_info=True)
            counters["errors"] += 1
        await asyncio.sleep(0.2)  # avoid Todoist 429

    prefix = "DRY-RUN" if dry_run else "BACKFILL"
    print(
        f"{prefix}: skipped={counters['skipped']}, updated={counters['updated']}, "
        f"not_found={counters['not_found']}, errors={counters['errors']}"
    )
    return counters


async def main(dry_run: bool) -> int:
    load_dotenv()
    get_settings.cache_clear()
    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Token bootstrap (mirrors migrate.py)
    async with session_factory() as session:
        stored_token, stored_expires_at = await load_token_from_kv(session)
    now_utc = datetime.now(timezone.utc)
    needs_refresh = (
        not stored_token
        or stored_expires_at is None
        or stored_expires_at <= now_utc
    )
    if needs_refresh:
        access_token, expires_at = await refresh_access_token(settings)
        async with session_factory() as session:
            await upsert_kv(session, KV_ACCESS_TOKEN_KEY, access_token)
            await upsert_kv(session, KV_EXPIRES_AT_KEY, expires_at.isoformat())
            await session.commit()
    else:
        access_token = stored_token
        expires_at = stored_expires_at

    token_state["access_token"] = access_token
    token_state["expires_at"] = expires_at

    zoho_client = ZohoClient(access_token=access_token)
    todoist_api = TodoistAPIAsync(token=settings.todoist_api_token)

    try:
        await run_backfill(session_factory, zoho_client, todoist_api, dry_run)
    finally:
        await todoist_api.close()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill Phase 09 descriptions onto existing synced Todoist tasks."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts; do not write descriptions to Todoist.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(dry_run=args.dry_run)))
