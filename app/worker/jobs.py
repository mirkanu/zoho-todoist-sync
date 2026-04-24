"""arq worker job: sync_task — the full Zoho <-> Todoist sync pipeline for one task.

Pipeline:
  1. Acquire SETNX Redis lock `lock:sync:{zoho_task_id}` (30s TTL) — defence-in-depth dedup.
  2. Fetch live Zoho record via ctx['zoho_client'].get_task().
  3. Look up sync_state row; if missing -> create Todoist task, write ID back to Zoho,
     insert sync_state + log action='sync'.
  4. Fetch live Todoist task via ctx['todoist_client'].get_task().
  5. Compute canonical_hash on both normalised views.
  6. SELECT FOR UPDATE the sync_state row (critical section).
  7. Compare hashes against last_hash -> echo_suppressed | sync | overwrite.
  8. Write to target via writer module, update state.last_hash + last_synced_at, log event.
  9. Release SETNX lock in finally.

Retry: transient API errors raise Retry(defer=RETRY_DELAYS[job_try]).
LWW direction: Zoho wins on simultaneous divergence (SYNC-11).
Bootstrap race (LOOP-5): sync-managed Todoist tasks carry [zoho:ID] footer; their
hash will match sync_state.last_hash after a legitimate sync so echo_suppressed
path catches the resulting self-triggered webhook.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from arq import Retry
from sqlalchemy import select

from app.core.hash import canonical_hash
from app.core.logging import get_logger
from app.core.normalise import NormalisedTask
from app.db.models import SyncEvent, SyncState
from app.todoist.client import (
    TodoistAPIError,
    TodoistNotFoundError,
    TodoistRateLimitError,
)
from app.todoist.normalise import extract_zoho_id, todoist_task_to_normalised
from app.todoist.writer import (
    complete_todoist_task,
    create_todoist_task,
    update_todoist_task,
)
from app.zoho.client import (
    ZohoAPIError,
    ZohoNotFoundError,
    ZohoRateLimitError,
)
from app.zoho.normalise import zoho_record_to_normalised
from app.zoho.state import token_state
from app.zoho.writer import (
    complete_zoho_task,
    update_zoho_task,
    write_todoist_id_to_zoho,
)

log = get_logger(__name__)

# arq job_try is 1-indexed. After attempt N raising Retry(defer=RETRY_DELAYS[N])
# the job re-runs with job_try=N+1. With max_tries=4 the 4th attempt is final.
RETRY_DELAYS: dict[int, int] = {1: 5, 2: 15, 3: 60}


async def sync_task(ctx: dict, zoho_task_id: str) -> None:
    """Full Zoho <-> Todoist sync pipeline for one task. arq job entry point.

    ctx keys (populated by WorkerSettings.on_startup + arq itself):
      - redis: ArqRedis (injected by arq)
      - session_factory: async_sessionmaker
      - zoho_client: ZohoClient
      - todoist_client: TodoistClient
      - job_try: int (injected by arq, 1-indexed)
    """
    redis = ctx["redis"]
    session_factory = ctx["session_factory"]
    zoho_client = ctx["zoho_client"]
    todoist_client = ctx["todoist_client"]
    job_try: int = ctx["job_try"]

    lock_key = f"lock:sync:{zoho_task_id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=30)
    if not acquired:
        log.warning("sync_task_lock_not_acquired", zoho_task_id=zoho_task_id)
        return  # reconciler will catch any missed sync within 15 min

    try:
        await _execute_sync(
            zoho_task_id, session_factory, zoho_client, todoist_client, job_try
        )
    except (ZohoRateLimitError, ZohoAPIError, TodoistRateLimitError, TodoistAPIError) as exc:
        delay = RETRY_DELAYS.get(job_try, 60)
        log.error(
            "sync_task_api_error_will_retry",
            zoho_task_id=zoho_task_id,
            attempt=job_try,
            delay=delay,
            error=str(exc),
        )
        raise Retry(defer=delay) from exc
    except ZohoNotFoundError:
        # 404 on the Zoho side — orphan handling is Phase 7 territory.
        # For Phase 5, log and return (no retry).
        log.warning("sync_task_zoho_not_found", zoho_task_id=zoho_task_id)
    except TodoistNotFoundError:
        log.warning("sync_task_todoist_not_found", zoho_task_id=zoho_task_id)
    finally:
        await redis.delete(lock_key)


async def _execute_sync(
    zoho_task_id: str,
    session_factory: Any,
    zoho_client: Any,
    todoist_client: Any,
    job_try: int,
) -> None:
    # [1] Fetch live Zoho state BEFORE any DB lock (Pitfall 2).
    zoho_record = await zoho_client.get_task(zoho_task_id)
    zoho_norm = zoho_record_to_normalised(zoho_record)

    # [2] Read sync_state row (no lock) — decide new-task vs. update path.
    async with session_factory() as session:
        result = await session.execute(
            select(SyncState).where(SyncState.zoho_task_id == zoho_task_id)
        )
        state = result.scalar_one_or_none()

    if state is None:
        await _handle_new_task(zoho_task_id, zoho_norm, todoist_client, session_factory)
        return

    # [3] Fetch live Todoist state.
    todoist_norm = await todoist_client.get_task(state.todoist_task_id)

    # [4] Compute canonical hashes.
    zoho_hash = canonical_hash(zoho_norm)
    todoist_hash = canonical_hash(todoist_norm)

    # [5] Critical section: SELECT FOR UPDATE + hash compare + conditional write + log.
    async with session_factory() as session:
        async with session.begin():
            locked = await session.execute(
                select(SyncState)
                .where(SyncState.zoho_task_id == zoho_task_id)
                .with_for_update()
            )
            state = locked.scalar_one()
            last_hash = state.last_hash

            # Echo suppression: both sides already match persisted hash.
            if zoho_hash == last_hash and todoist_hash == last_hash:
                session.add(SyncEvent(
                    zoho_task_id=zoho_task_id,
                    action="echo_suppressed",
                    source="worker",
                    detail={"hash": last_hash[:8]},
                ))
                log.info("sync_task_echo_suppressed", zoho_task_id=zoho_task_id)
                return

            # Direction + action decision (LWW: Zoho wins on simultaneous divergence).
            if zoho_hash != last_hash and todoist_hash != last_hash:
                action = "overwrite"
                direction = "zoho_to_todoist"
                target_norm = zoho_norm
                new_hash = zoho_hash
            elif zoho_hash != last_hash:
                action = "sync"
                direction = "zoho_to_todoist"
                target_norm = zoho_norm
                new_hash = zoho_hash
            else:
                action = "sync"
                direction = "todoist_to_zoho"
                target_norm = todoist_norm
                new_hash = todoist_hash

            await _apply_write(
                direction, state, target_norm, zoho_task_id, todoist_client
            )

            state.last_hash = new_hash
            state.last_synced_at = datetime.now(timezone.utc)
            session.add(SyncEvent(
                zoho_task_id=zoho_task_id,
                action=action,
                source="worker",
                detail={"direction": direction, "new_hash": new_hash[:8]},
            ))
            log.info(
                "sync_task_written",
                zoho_task_id=zoho_task_id,
                action=action,
                direction=direction,
            )


async def _apply_write(
    direction: str,
    state: Any,
    target_norm: NormalisedTask,
    zoho_task_id: str,
    todoist_client: Any,
) -> None:
    """Route write to the correct target based on direction and is_completed."""
    if direction == "zoho_to_todoist":
        if target_norm.is_completed:
            await complete_todoist_task(state.todoist_task_id, todoist_client._api)
        else:
            await update_todoist_task(state.todoist_task_id, target_norm, todoist_client._api)
    else:  # todoist_to_zoho
        access_token = token_state["access_token"]
        if target_norm.is_completed:
            await complete_zoho_task(zoho_task_id, access_token)
        else:
            await update_zoho_task(zoho_task_id, target_norm, access_token)


async def _handle_new_task(
    zoho_task_id: str,
    zoho_norm: NormalisedTask,
    todoist_client: Any,
    session_factory: Any,
) -> None:
    """No sync_state row -> this is a new Zoho task. Create in Todoist + link + log."""
    todoist_id = await create_todoist_task(zoho_norm, zoho_task_id, todoist_client._api)
    await write_todoist_id_to_zoho(zoho_task_id, todoist_id, token_state["access_token"])
    new_hash = canonical_hash(zoho_norm)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        async with session.begin():
            session.add(SyncState(
                zoho_task_id=zoho_task_id,
                todoist_task_id=todoist_id,
                last_hash=new_hash,
                last_synced_at=now,
                orphan_check_count=0,
            ))
            session.add(SyncEvent(
                zoho_task_id=zoho_task_id,
                action="sync",
                source="worker",
                detail={"direction": "zoho_to_todoist", "created": True},
            ))
    log.info(
        "sync_task_new_task_created",
        zoho_task_id=zoho_task_id,
        todoist_id=todoist_id,
    )
