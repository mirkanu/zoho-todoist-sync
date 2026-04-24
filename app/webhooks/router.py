"""FastAPI webhook router: Zoho (notification-only) and Todoist (stub; Plan 02 fills in).

Both endpoints return 200 before any DB write or external API call. The only
synchronous work permitted: payload parsing and (for Todoist, Plan 02) HMAC
verification. All sync logic lives in the worker.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.core.logging import get_logger
from app.worker.enqueue import enqueue_sync

log = get_logger(__name__)
router = APIRouter()


@router.post("/zoho")
async def zoho_webhook(request: Request):
    """Zoho notification-only webhook: enqueue sync_task with a 2s defer (LOOP-4).

    Payload contract (SYNC-4): {"module": "Tasks", "ids": [<task_id>]}
    We never use field values from the payload — the worker refetches from Zoho.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    module = payload.get("module")
    ids = payload.get("ids")
    if not module or ids is None:
        raise HTTPException(status_code=400, detail="missing module or ids")

    # Open question A3: some Zoho webhook configs may deliver `ids` as a bare
    # string instead of a list. Accept both and normalise.
    if isinstance(ids, list):
        if not ids:
            raise HTTPException(status_code=400, detail="ids is empty")
        zoho_task_id = str(ids[0])
    else:
        zoho_task_id = str(ids)

    settings = get_settings()
    redis = request.app.state.redis
    await enqueue_sync(
        redis, zoho_task_id, defer_secs=settings.zoho_job_defer_secs
    )
    log.info(
        "zoho_webhook_enqueued",
        zoho_task_id=zoho_task_id,
        defer_secs=settings.zoho_job_defer_secs,
    )
    return {"ok": True}


@router.post("/todoist")
async def todoist_webhook(request: Request):
    """Todoist webhook — STUB. Plan 06-02 replaces this body with HMAC
    verification + event_name dispatch + sync_state lookup + enqueue_sync.

    Returns 200 for now so Plan 01's path-registration check passes without
    introducing side-effects.
    """
    return {"ok": True}
