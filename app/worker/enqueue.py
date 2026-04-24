"""Enqueue helper used by webhook handlers (Phase 6) and reconciler (Phase 7).

Callers must pass `defer_secs=settings.zoho_job_defer_secs` for Zoho-triggered
jobs to mitigate the Zoho write->webhook stale-read race (LOOP-4). For
Todoist-triggered jobs pass `defer_secs=0`. This plan only guarantees the
parameter is forwarded to arq as `_defer_by`; Phase 6 is responsible for
choosing the correct value at each call site.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from arq import ArqRedis

log = get_logger(__name__)


async def enqueue_sync(
    redis: "ArqRedis",
    zoho_task_id: str,
    defer_secs: int = 0,
) -> None:
    """Enqueue sync_task with per-task dedup (_job_id) and optional defer (_defer_by).

    Returns None in both the enqueued and deduplicated paths; the deduplicated
    path additionally emits a WARN log (SYNC-10).
    """
    job = await redis.enqueue_job(
        "sync_task",
        zoho_task_id,
        _job_id=f"sync:{zoho_task_id}",
        _defer_by=defer_secs,
    )
    if job is None:
        log.warning(
            "sync_task_dedup_dropped",
            zoho_task_id=zoho_task_id,
            defer_secs=defer_secs,
        )
    else:
        log.info(
            "sync_task_enqueued",
            zoho_task_id=zoho_task_id,
            defer_secs=defer_secs,
        )
