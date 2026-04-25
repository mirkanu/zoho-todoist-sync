"""Daily midnight UTC cron: 90-day sync_events cleanup + Todoist summary task. OBS-3, OBS-4."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func, delete

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import SyncEvent

log = get_logger(__name__)

RETENTION_DAYS = 90
SUMMARY_WINDOW_HOURS = 24


async def daily_summary(ctx: dict) -> None:
    """Midnight-UTC cron: 90-day cleanup, then create + complete a Todoist summary task.

    Order matters per D-09: DELETE old events, COMMIT, THEN COUNT for summary
    so post-cleanup numbers are reflected in the summary task description.
    """
    session_factory = ctx["session_factory"]
    todoist_client = ctx["todoist_client"]
    settings = get_settings()

    log.info("daily_summary_start")

    # Step 1 (D-09 ordering): cleanup events older than 90 days, commit
    cutoff_90d = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    async with session_factory() as session:
        del_result = await session.execute(
            delete(SyncEvent).where(SyncEvent.created_at < cutoff_90d)
        )
        await session.commit()
    deleted_count = getattr(del_result, "rowcount", 0) or 0

    # Step 2: count last 24h (post-cleanup state)
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=SUMMARY_WINDOW_HOURS)
    async with session_factory() as session:
        syncs = (await session.execute(
            select(func.count()).select_from(SyncEvent).where(
                SyncEvent.action == "sync",
                SyncEvent.created_at > cutoff_24h,
            )
        )).scalar_one()
        errors = (await session.execute(
            select(func.count()).select_from(SyncEvent).where(
                SyncEvent.action == "error",
                SyncEvent.created_at > cutoff_24h,
            )
        )).scalar_one()
        echoes = (await session.execute(
            select(func.count()).select_from(SyncEvent).where(
                SyncEvent.action == "echo_suppressed",
                SyncEvent.created_at > cutoff_24h,
            )
        )).scalar_one()

    # Step 3 (D-08): create summary task with exact format
    today = datetime.now(timezone.utc).date().isoformat()
    title = f"Sync summary: {today}"
    description = f"{syncs} syncs, {errors} errors, {echoes} echoes suppressed"

    # Step 4 (D-07): add task in synced project, then immediately complete it
    task = await todoist_client._api.add_task(
        content=title,
        description=description,
        project_id=settings.todoist_project_id,
    )
    await todoist_client._api.complete_task(task.id)

    log.info(
        "daily_summary_complete",
        deleted=deleted_count,
        syncs=syncs,
        errors=errors,
        echoes=echoes,
        todoist_task_id=task.id,
    )
