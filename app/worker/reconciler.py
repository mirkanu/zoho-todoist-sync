"""Periodic reconciliation cron jobs. SEED-5, SEED-7."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.hash import canonical_hash
from app.core.logging import get_logger
from app.db.models import SyncState
from app.todoist.normalise import extract_zoho_id
from app.todoist.sync_manager import load_sync_token, save_sync_token
from app.worker.enqueue import enqueue_sync
from app.zoho.client import ZohoAPIError
from app.zoho.normalise import zoho_record_to_normalised
from app.zoho.token_manager import upsert_kv

log = get_logger(__name__)

KV_RECONCILER_LAST_RUN = "reconciler_last_run"
RECONCILE_LOOKBACK_MINUTES = 20


async def reconcile_sweep(ctx: dict) -> None:
    """Periodic sweep: fetch Zoho modified-since tasks and Todoist incremental delta,
    enqueue sync_task for any task with a hash mismatch. SEED-5, SEED-7.

    Does NOT swallow exceptions — arq cron timeout/retry will handle them.
    """
    redis = ctx["redis"]
    session_factory = ctx["session_factory"]
    zoho_client = ctx["zoho_client"]
    todoist_client = ctx["todoist_client"]

    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(minutes=RECONCILE_LOOKBACK_MINUTES)

    log.info("reconcile_sweep_start", since=since.isoformat())

    # ------------------------------------------------------------------
    # Zoho side: fetch modified tasks, enqueue on hash mismatch
    # ------------------------------------------------------------------
    zoho_records = await zoho_client.fetch_tasks_modified_since(since, settings.zoho_user_id)

    for record in zoho_records:
        zoho_task_id = str(record.get("id"))
        zoho_norm = zoho_record_to_normalised(record, settings.zoho_terminal_statuses_list)
        zoho_hash = canonical_hash(zoho_norm)

        async with session_factory() as session:
            result = await session.execute(
                select(SyncState).where(SyncState.zoho_task_id == zoho_task_id)
            )
            state = result.scalar_one_or_none()

        if state is None or state.last_hash != zoho_hash:
            log.info("reconcile_zoho_enqueued", zoho_task_id=zoho_task_id)
            await enqueue_sync(redis, zoho_task_id, defer_secs=0)

    # ------------------------------------------------------------------
    # Todoist side: incremental delta via sync_token
    # ------------------------------------------------------------------
    async with session_factory() as session:
        stored_token = await load_sync_token(session)

    items, new_token = await todoist_client.fetch_sync_delta(
        sync_token=stored_token,
        project_id=settings.todoist_project_id,
    )

    # SEED-7: persist token BEFORE processing items (crash-safe)
    async with session_factory() as session:
        await save_sync_token(session, new_token)

    for item in items:
        if item.get("is_deleted"):
            log.info("reconcile_todoist_deleted_skipped", todoist_id=item.get("id"))
            continue
        zoho_id = extract_zoho_id(item.get("description"))
        if zoho_id is None:
            log.info("reconcile_todoist_no_footer_skipped", todoist_id=item.get("id"))
            continue
        await enqueue_sync(redis, zoho_id, defer_secs=0)

    # ------------------------------------------------------------------
    # Persist last-run timestamp
    # ------------------------------------------------------------------
    async with session_factory() as session:
        await upsert_kv(session, KV_RECONCILER_LAST_RUN, datetime.now(timezone.utc).isoformat())
        await session.commit()

    log.info(
        "reconcile_sweep_complete",
        zoho_count=len(zoho_records),
        todoist_count=len(items),
    )
