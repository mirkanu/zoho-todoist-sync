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
from app.nirvana.client import NirvanaAPIError, NirvanaNotFoundError, NirvanaRateLimitError
from app.nirvana.normalise import nirvana_task_to_normalised
from app.todoist.client import TodoistAPIError, TodoistNotFoundError, TodoistRateLimitError
from app.todoist.sync_manager import load_sync_token, save_sync_token
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

_ZOHO_WEBHOOK_URL = "https://zoho.gsdlabs.dev/webhooks/zoho"
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
                    SyncState.external_task_id == todoist_id
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


KV_NIRVANA_POLL_LAST_RUN = "nirvana_poll_sweep_last_run"
NIRVANA_POLL_PAGE_SIZE = 200  # get_tasks() per-page size (D-04)
NIRVANA_POLL_MAX_PAGES = 25  # safety cap: 5000 Zoho-tagged tasks, defensive only


async def nirvana_poll_sweep(ctx: dict) -> None:
    """Nirvana has no webhook equivalent (D-09) — this cron is the ONLY signal for
    Nirvana-side changes. Always registered (Plan 07); no-ops when TASK_PROVIDER
    is not 'nirvana', mirroring the D-13 pattern used for the Todoist webhook route
    so switching providers never requires touching cron registration.

    Poll + diff against sync_state, mirroring reconcile_sweep's Zoho-side diff
    loop. Scoped to tags=["Zoho"] (every sync-managed task carries this tag,
    see app.nirvana.writer.BASE_TAGS) rather than the whole account, and
    paginated via NirvanaClient.get_tasks_paginated — confirmed live
    2026-07-28 that a raw unfiltered single-page scan silently missed tasks
    once the account grew past 200 total items (551 total vs. ~270 active at
    time of writing); the original 200-item "hard cap" assumption from
    RESEARCH.md Pitfall 4 was wrong, offset/has_more pagination is real and
    supported.
    """
    settings = get_settings()
    if settings.task_provider != "nirvana":
        log.debug("nirvana_poll_sweep_inactive", active_provider=settings.task_provider)
        return

    redis = ctx["redis"]
    session_factory = ctx["session_factory"]
    task_provider = ctx["task_provider"]

    counts = await task_provider.get_task_counts()
    log.info("nirvana_poll_sweep_start", counts=counts)

    tasks = await task_provider.get_tasks_paginated(
        page_size=NIRVANA_POLL_PAGE_SIZE, max_pages=NIRVANA_POLL_MAX_PAGES, tags=["Zoho"]
    )
    if len(tasks) >= NIRVANA_POLL_PAGE_SIZE * NIRVANA_POLL_MAX_PAGES:
        log.warning(
            "nirvana_poll_sweep_cap_hit",
            limit=NIRVANA_POLL_PAGE_SIZE * NIRVANA_POLL_MAX_PAGES,
            hint="Zoho-tagged tasks may exceed the pagination safety cap; some may be missed this cycle.",
        )

    enqueued = 0
    for raw_task in tasks:
        nirvana_id = str(raw_task.get("id"))
        norm = nirvana_task_to_normalised(raw_task)
        current_hash = canonical_hash(norm)

        async with session_factory() as session:
            result = await session.execute(
                select(SyncState).where(
                    SyncState.external_task_id == nirvana_id,
                    SyncState.provider == "nirvana",
                )
            )
            state = result.scalar_one_or_none()

        if state is None:
            log.debug("nirvana_poll_sweep_unmatched_task", nirvana_id=nirvana_id)
            continue

        if state.last_hash != current_hash:
            log.info("nirvana_poll_sweep_enqueued", zoho_task_id=state.zoho_task_id)
            await enqueue_sync(redis, state.zoho_task_id, defer_secs=0, source="nirvana_poll")
            enqueued += 1

    async with session_factory() as session:
        await upsert_kv(session, KV_NIRVANA_POLL_LAST_RUN, datetime.now(timezone.utc).isoformat())
        await session.commit()

    log.info("nirvana_poll_sweep_complete", task_count=len(tasks), enqueued=enqueued)


def _provider_client_for_row(ctx: dict, provider: str) -> Any:
    """Select the client matching a sync_state row's OWN `provider` column —
    never the globally active ctx["task_provider"]. Mixed-provider state
    (old provider='todoist' rows coexisting with new provider='nirvana' rows)
    is the normal condition during and after a migration cutover; using the
    wrong client for a row always 404s, and orphan_sweep's two-cycle
    confirmation turns that false-404 into a real deletion. This caused a
    real incident on 2026-07-28 (64 Zoho tasks deleted, recovered from
    Zoho's Recycle Bin) before this fix.
    """
    if provider == "todoist":
        return ctx["todoist_client"]
    if provider == "nirvana":
        return ctx["nirvana_client"]
    # Defensive fallback for any unexpected/legacy provider value — mirrors
    # the pre-fix behavior rather than crashing, but should never trigger in
    # practice since provider is DB-constrained to ('todoist', 'nirvana').
    return ctx["task_provider"]


async def orphan_sweep(ctx: dict) -> None:
    """Hourly sweep: check all sync_state rows for orphaned task pairs.

    Two-cycle confirmation (EDGE-5): first detection increments orphan_check_count;
    second consecutive detection triggers deletion + notification + cleanup.
    Rate-limit errors skip the row (not counted as 404). SEED-6.
    """
    session_factory = ctx["session_factory"]
    zoho_client = ctx["zoho_client"]
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
        external_task = None
        zoho_data: dict | None = None

        # ------------------------------------------------------------------
        # Zoho check: existence + ownership (EDGE-1 reassignment detection)
        # ------------------------------------------------------------------
        try:
            response = await zoho_client.get_task(state.zoho_task_id)
            data = (response.get("data") or [{}])[0]
            zoho_data = data
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
        # External (Todoist/Nirvana) check: existence
        # ------------------------------------------------------------------
        provider_client = _provider_client_for_row(ctx, state.provider)
        try:
            external_task = await provider_client.fetch(state.external_task_id)
        except (TodoistNotFoundError, NirvanaNotFoundError):
            todoist_missing = True
        except (TodoistRateLimitError, TodoistAPIError, NirvanaRateLimitError, NirvanaAPIError) as exc:
            log.warning(
                "orphan_sweep_external_api_error",
                external_task_id=state.external_task_id,
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
        await _handle_orphan(
            state, zoho_missing, todoist_missing, ctx,
            todoist_task=external_task, zoho_data=zoho_data,
        )

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
    todoist_task: Any = None,
    zoho_data: dict | None = None,
) -> None:
    """Delete the live counterpart of a confirmed orphan pair, send notification,
    delete the sync_state row, and log a SyncEvent. EDGE-1, EDGE-2, EDGE-6.

    Note: task_provider.delete() and delete_zoho_task already send Resend notifications
    internally (EDGE-6). No double-notification here.
    """
    session_factory = ctx["session_factory"]

    if zoho_missing:
        # Zoho task gone or reassigned (EDGE-1) → delete external counterpart.
        # external_task is a NormalisedTask (task_provider.fetch() already
        # normalises) — use its .title, not a raw SDK object's .content.
        # Must use the client matching THIS row's own provider, not whichever
        # provider is globally active — see _provider_client_for_row.
        task_name = getattr(todoist_task, "title", None) if todoist_task is not None else None
        try:
            provider_client = _provider_client_for_row(ctx, state.provider)
            await provider_client.delete(state.external_task_id, task_name=task_name)
        except Exception as exc:
            log.error(
                "orphan_external_delete_failed",
                external_task_id=state.external_task_id,
                error=str(exc),
            )

    if todoist_missing:
        # Todoist task gone (EDGE-2) → delete Zoho counterpart
        # zoho_data is the live record fetched earlier in the sweep (Subject = task name)
        task_name = (zoho_data or {}).get("Subject")
        try:
            access_token = token_state["access_token"]
            await delete_zoho_task(state.zoho_task_id, access_token, task_name=task_name)
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
                    "external_task_id": state.external_task_id,
                    "zoho_missing": zoho_missing,
                    "todoist_missing": todoist_missing,
                },
            ))

    log.info("orphan_resolved", zoho_task_id=state.zoho_task_id)
