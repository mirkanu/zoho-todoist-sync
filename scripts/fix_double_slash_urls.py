"""One-shot fix: replace double-slash URLs in Todoist task descriptions.

Targets descriptions containing 'crm//tab/Tasks/' and replaces with the
correct org-scoped URL. Idempotent — skips tasks that don't have the bad URL.

Usage:
    python scripts/fix_double_slash_urls.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from todoist_api_python.api_async import TodoistAPIAsync

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import SyncState

log = get_logger(__name__)

BAD_PATTERN = "crm//tab/Tasks/"


async def run_fix(session_factory, todoist_api: TodoistAPIAsync, org_id: str, dry_run: bool) -> dict[str, int]:
    counters = {"fixed": 0, "skipped": 0, "errors": 0}
    good_prefix = f"crm/{org_id}/tab/Tasks/"

    async with session_factory() as session:
        rows = (await session.execute(select(SyncState))).scalars().all()

    log.info("fix_start", total=len(rows), dry_run=dry_run)

    for row in rows:
        try:
            task = await todoist_api.get_task(row.todoist_task_id)
            if not task.description or BAD_PATTERN not in task.description:
                counters["skipped"] += 1
                continue
            fixed_desc = task.description.replace(BAD_PATTERN, good_prefix)
            if not dry_run:
                await todoist_api.update_task(row.todoist_task_id, description=fixed_desc)
            log.info("fix_updated", todoist_id=row.todoist_task_id, zoho_id=row.zoho_task_id, dry_run=dry_run)
            counters["fixed"] += 1
        except Exception as exc:
            log.error("fix_error", todoist_id=row.todoist_task_id, error=str(exc))
            counters["errors"] += 1
        await asyncio.sleep(0.3)

    prefix = "DRY-RUN" if dry_run else "FIXED"
    print(f"{prefix}: fixed={counters['fixed']}, skipped={counters['skipped']}, errors={counters['errors']}")
    return counters


async def main(dry_run: bool) -> int:
    load_dotenv()
    get_settings.cache_clear()
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.zoho_org_id:
        print("ERROR: ZOHO_ORG_ID is not set — cannot build correct URL")
        return 1

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    todoist_api = TodoistAPIAsync(token=settings.todoist_api_token)
    try:
        counters = await run_fix(session_factory, todoist_api, settings.zoho_org_id, dry_run)
    finally:
        await todoist_api.close()
        await engine.dispose()
    return 1 if counters["errors"] > 0 else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix double-slash Zoho URLs in Todoist task descriptions.")
    parser.add_argument("--dry-run", action="store_true", help="Print counts; do not write to Todoist.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(dry_run=args.dry_run)))
