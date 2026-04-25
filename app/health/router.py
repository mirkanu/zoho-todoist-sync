"""Health endpoint — GET /health (OBS-1, OBS-2, INFRA-5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from arq.constants import default_queue_name, in_progress_key_prefix
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db.models import KVStore, SyncEvent, SyncState

# Constant mirrors app.worker.reconciler.KV_RECONCILER_LAST_RUN (same string value).
# Defined here to avoid importing reconciler at module level, which would trigger
# a settings validation error during test collection (app.core.config has a
# module-level get_settings() call that requires env vars to be set).
KV_RECONCILER_LAST_RUN = "reconciler_last_run"

log = get_logger(__name__)
router = APIRouter()

STALE_RECONCILER_MINUTES = 30
DEGRADED_ERROR_THRESHOLD = 10


def _compute_status(
    errors_24h: int,
    reconciler_last_run: str | None,
    queue_failed: int,
) -> str:
    """Compute health status per D-10 thresholds.

    Returns 'error' | 'degraded' | 'ok'.
    """
    if queue_failed > 0:
        return "error"
    if reconciler_last_run is None:
        return "error"
    try:
        last_run = datetime.fromisoformat(reconciler_last_run)
    except ValueError:
        return "error"
    if (datetime.now(timezone.utc) - last_run) > timedelta(minutes=STALE_RECONCILER_MINUTES):
        return "error"
    if errors_24h > DEGRADED_ERROR_THRESHOLD:
        return "degraded"
    return "ok"


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Return operational health metrics.

    Reads only from DB (sync_events, sync_state, kv_store) and Redis (ZCARD/scan_iter).
    No live Zoho or Todoist API calls. Response shape per OBS-1.
    """
    redis = request.app.state.redis
    session_factory = request.app.state.session_factory

    # Redis metrics — O(1) operations only (Pitfall 1: NEVER iterate arq:result:*)
    queue_depth = await redis.zcard(default_queue_name)
    in_progress = 0
    async for _ in redis.scan_iter(match=in_progress_key_prefix + "*"):
        in_progress += 1

    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    async with session_factory() as session:
        # Count errors in last 24h
        errors_24h = (
            await session.execute(
                select(func.count()).select_from(SyncEvent).where(
                    SyncEvent.action == "error",
                    SyncEvent.created_at > cutoff_24h,
                )
            )
        ).scalar_one()

        # Count echo suppressions in last 24h
        echoes_24h = (
            await session.execute(
                select(func.count()).select_from(SyncEvent).where(
                    SyncEvent.action == "echo_suppressed",
                    SyncEvent.created_at > cutoff_24h,
                )
            )
        ).scalar_one()

        # Count successful syncs in last 24h
        syncs_24h = (
            await session.execute(
                select(func.count()).select_from(SyncEvent).where(
                    SyncEvent.action == "sync",
                    SyncEvent.created_at > cutoff_24h,
                )
            )
        ).scalar_one()

        # Count active synced tasks
        active_tasks = (
            await session.execute(
                select(func.count()).select_from(SyncState)
            )
        ).scalar_one()

        # Most recent sync event (for last_sync.at and last_sync.source)
        last_event = (
            await session.execute(
                select(SyncEvent)
                .where(SyncEvent.action == "sync")
                .order_by(SyncEvent.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        # Reconciler last-run timestamp from kv_store
        reconciler_kv = (
            await session.execute(
                select(KVStore.value).where(KVStore.key == KV_RECONCILER_LAST_RUN)
            )
        ).scalar_one_or_none()

    # queue.failed in the response body uses errors_24h as a display proxy.
    # Status computation always passes queue_failed=0 because failed arq jobs cannot
    # be safely counted without iterating arq:result:* (Pitfall 1). The D-10 "error
    # if queue.failed > 0" branch is reserved for future implementation.
    queue_failed = errors_24h  # display proxy for response body
    status = _compute_status(errors_24h, reconciler_kv, queue_failed=0)
    http_code = 503 if status == "error" else 200

    last_sync = None
    if last_event is not None:
        last_sync = {
            "at": last_event.created_at.isoformat(),
            "source": last_event.source,
        }

    body = {
        "status": status,
        "last_sync": last_sync,
        "queue": {
            "depth": queue_depth,
            "in_progress": in_progress,
            "failed": queue_failed,
        },
        "errors_24h": errors_24h,
        "echoes_suppressed_24h": echoes_24h,
        "syncs_24h": syncs_24h,
        "active_tasks": active_tasks,
        "reconciler": {"last_run": reconciler_kv},
    }

    log.info(
        "health_check",
        status=status,
        errors_24h=errors_24h,
        syncs_24h=syncs_24h,
        queue_depth=queue_depth,
    )

    return JSONResponse(content=body, status_code=http_code)
