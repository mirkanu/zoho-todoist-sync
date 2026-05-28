"""Periodic reconciliation cron jobs. SEED-5, SEED-6, SEED-7."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.hash import canonical_hash
from app.core.logging import get_logger
from app.core.notifications import send_deletion_notification
from app.db.models import SyncEvent, SyncState
from app.todoist.client import TodoistAPIError, TodoistNotFoundError, TodoistRateLimitError
from app.todoist.sync_manager import load_sync_token, save_sync_token
from app.todoist.writer import delete_todoist_task
from app.worker.enqueue import enqueue_sync
from app.zoho.client import ZohoAPIError, ZohoNotFoundError, ZohoRateLimitError
from app.zoho.normalise import zoho_record_to_normalised
from app.zoho.state import token_state
from app.zoho.token_manager import upsert_kv
from app.zoho.writer import delete_zoho_task

import httpx

log = get_logger(__name__)

KV_RECONCILER_LAST_RUN = "reconciler_last_run"
KV_ORPHAN_SWEEP_LAST_RUN = "orphan_sweep_last_run"
RECONCILE_LOOKBACK_MINUTES = 20

_ZOHO_WEBHOOK_URL = "https://zoho-sync.gsdlabs.dev/webhooks/zoho"
_ZOHO_CHANNEL_ID = "1"


async def renew_zoho_webhook(ctx: dict) -> None:
    """Re-register the Zoho notification channel every 45 min.

    Zoho caps channel_expiry to ~2 hours (bound by the OAuth access token TTL).
    Re-posting with the same channel_id extends the expiry.
    """
    settings = get_settings()
    access_token = token_state["access_token"]
    payload = {
        "watch": [{
            "channel_id": _ZOHO_CHANNEL_ID,
            "events": ["Tasks.create", "Tasks.edit", "Tasks.delete"],
            "token": "zoho-sync-webhook-token",
            "notify_url": _ZOHO_WEBHOOK_URL,
            "channel_expiry": (
                datetime.now(timezone.utc) + timedelta(hours=2)
            ).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        }]
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://www.zohoapis.eu/crm/v6/actions/watch",
            headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
            json=payload,
        )
    result = resp.json()
    status = (result.get("watch") or [{}])[0].get("code", "UNKNOWN")
    if status == "SUCCESS":
        log.info("zoho_webhook_renewed")
    else:
        log.warning("zoho_webhook_renewal_failed", response=result)


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

    # Sync client token from global state (proactive_refresh_loop updates token_state only).
    zoho_client.access_token = token_state["access_token"]

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
            await enqueue_sync(redis, zoho_task_id, defer_secs=0, source="reconciler")

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
        todoist_id = str(item.get("id", ""))
        async with session_factory() as session:
            result = await session.execute(
                select(SyncState.zoho_task_id).where(
                    SyncState.todoist_task_id == todoist_id
                )
            )
            zoho_id = result.scalar_one_or_none()
        if zoho_id is None:
            log.debug("reconcile_todoist_not_in_sync_state", todoist_id=todoist_id)
            continue
        await enqueue_sync(redis, zoho_id, defer_secs=0, source="reconciler")

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


async def orphan_sweep(ctx: dict) -> None:
    """Hourly sweep: check all sync_state rows for orphaned task pairs.

    Two-cycle confirmation (EDGE-5): first detection increments orphan_check_count;
    second consecutive detection triggers deletion + notification + cleanup.
    Rate-limit errors skip the row (not counted as 404). SEED-6.
    """
    session_factory = ctx["session_factory"]
    zoho_client = ctx["zoho_client"]
    todoist_client = ctx["todoist_client"]
    settings = get_settings()

    log.info("orphan_sweep_start")

    # Sync client token from global state (proactive_refresh_loop updates token_state only).
    zoho_client.access_token = token_state["access_token"]

    # Load all sync_state rows for the scan
    async with session_factory() as session:
        result = await session.execute(select(SyncState))
        rows = result.scalars().all()

    for state in rows:
        zoho_missing = False
        todoist_missing = False
        todoist_task = None

        # ------------------------------------------------------------------
        # Zoho check: existence + ownership (EDGE-1 reassignment detection)
        # ------------------------------------------------------------------
        try:
            response = await zoho_client.get_task(state.zoho_task_id)
            data = (response.get("data") or [{}])[0]
            owner_raw = (data.get("Owner") or {}).get("id", "")
            # V5 input validation: ensure str before comparison (T-7-07)
            if not isinstance(owner_raw, str):
                owner_raw = str(owner_raw) if owner_raw is not None else ""
            if owner_raw != settings.zoho_user_id:
                zoho_missing = True  # EDGE-1: reassignment treated as missing
                log.warning(
                    "orphan_sweep_zoho_reassigned",
                    zoho_task_id=state.zoho_task_id,
                    new_owner=owner_raw,
                )
        except ZohoNotFoundError:
            zoho_missing = True
        except (ZohoRateLimitError, ZohoAPIError) as exc:
            log.warning(
                "orphan_sweep_zoho_api_error",
                zoho_task_id=state.zoho_task_id,
                error=str(exc),
            )
            continue  # SKIP — do not count as 404; retry next sweep

        # ------------------------------------------------------------------
        # Todoist check: existence
        # ------------------------------------------------------------------
        try:
            todoist_task = await todoist_client.fetch_todoist_task(state.todoist_task_id)
        except TodoistNotFoundError:
            todoist_missing = True
        except (TodoistRateLimitError, TodoistAPIError) as exc:
            log.warning(
                "orphan_sweep_todoist_api_error",
                todoist_task_id=state.todoist_task_id,
                error=str(exc),
            )
            continue  # SKIP — do not count as 404; retry next sweep

        # ------------------------------------------------------------------
        # Healthy path: both sides present
        # ------------------------------------------------------------------
        if not zoho_missing and not todoist_missing:
            # Recovery: reset orphan_check_count if it was elevated
            if state.orphan_check_count > 0:
                async with session_factory() as sess:
                    async with sess.begin():
                        locked = await sess.get(SyncState, state.zoho_task_id)
                        if locked is not None:
                            locked.orphan_check_count = 0

            # Drift detection: if Zoho hash has diverged from last_hash, re-enqueue sync.
            # `data` is in scope from the Zoho fetch try-block above.
            zoho_norm = zoho_record_to_normalised(data, settings.zoho_terminal_statuses_list)
            zoho_hash = canonical_hash(zoho_norm)
            if zoho_hash != state.last_hash:
                log.warning(
                    "orphan_sweep_drift_detected",
                    zoho_task_id=state.zoho_task_id,
                    stored_hash_prefix=state.last_hash[:8] if state.last_hash else "None",
                    current_hash_prefix=zoho_hash[:8],
                )
                await enqueue_sync(
                    ctx["redis"],
                    state.zoho_task_id,
                    defer_secs=0,
                    source="orphan_drift",
                )
            continue

        # ------------------------------------------------------------------
        # Two-cycle confirmation (EDGE-5): first detection — increment counter
        # ------------------------------------------------------------------
        if state.orphan_check_count < 1:
            log.warning(
                "orphan_first_cycle",
                zoho_task_id=state.zoho_task_id,
                zoho_missing=zoho_missing,
                todoist_missing=todoist_missing,
            )
            async with session_factory() as sess:
                async with sess.begin():
                    locked = await sess.get(SyncState, state.zoho_task_id)
                    if locked is not None:
                        locked.orphan_check_count += 1
            continue

        # Second consecutive detection → orphan confirmed; handle it
        await _handle_orphan(state, zoho_missing, todoist_missing, ctx)

    # ------------------------------------------------------------------
    # Persist last-run timestamp
    # ------------------------------------------------------------------
    async with session_factory() as session:
        await upsert_kv(session, KV_ORPHAN_SWEEP_LAST_RUN, datetime.now(timezone.utc).isoformat())
        await session.commit()

    log.info("orphan_sweep_complete", row_count=len(rows))


async def _handle_orphan(
    state: Any,
    zoho_missing: bool,
    todoist_missing: bool,
    ctx: dict,
) -> None:
    """Delete the live counterpart of a confirmed orphan pair, send notification,
    delete the sync_state row, and log a SyncEvent. EDGE-1, EDGE-2, EDGE-6.

    Note: delete_todoist_task and delete_zoho_task already send Resend notifications
    internally (EDGE-6). No double-notification here.
    """
    session_factory = ctx["session_factory"]

    if zoho_missing:
        # Zoho task gone or reassigned (EDGE-1) → delete Todoist counterpart
        try:
            await delete_todoist_task(state.todoist_task_id, ctx["todoist_client"]._api)
        except Exception as exc:
            log.error(
                "orphan_todoist_delete_failed",
                todoist_task_id=state.todoist_task_id,
                error=str(exc),
            )

    if todoist_missing:
        # Todoist task gone (EDGE-2) → delete Zoho counterpart
        try:
            access_token = token_state["access_token"]
            await delete_zoho_task(state.zoho_task_id, access_token)
        except Exception as exc:
            log.error(
                "orphan_zoho_delete_failed",
                zoho_task_id=state.zoho_task_id,
                error=str(exc),
            )

    # Delete sync_state row + log SyncEvent in a single transaction
    async with session_factory() as session:
        async with session.begin():
            row = await session.get(SyncState, state.zoho_task_id)
            if row is not None:
                await session.delete(row)
            session.add(SyncEvent(
                zoho_task_id=state.zoho_task_id,
                action="orphan",
                source="reconciler",
                detail={
                    "todoist_task_id": state.todoist_task_id,
                    "zoho_missing": zoho_missing,
                    "todoist_missing": todoist_missing,
                },
            ))

    log.info("orphan_resolved", zoho_task_id=state.zoho_task_id)
