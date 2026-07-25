"""FastAPI webhook router: Zoho (notification-only) and Todoist (HMAC + event dispatch).

Both endpoints return 200 before any DB write or external API call. The only
synchronous work permitted: payload parsing and HMAC verification. All sync
logic lives in the worker.
"""
from __future__ import annotations

import base64
import hashlib
import hmac

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import SyncState
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
        redis, zoho_task_id, defer_secs=settings.zoho_job_defer_secs, source="zoho_webhook"
    )
    log.info(
        "zoho_webhook_enqueued",
        zoho_task_id=zoho_task_id,
        defer_secs=settings.zoho_job_defer_secs,
    )
    return {"ok": True}


async def _lookup_zoho_id(session_factory, todoist_task_id: str) -> str | None:
    """Single indexed read of sync_state; returns zoho_task_id or None.

    This is the ONLY DB operation permitted inside a webhook handler. The
    column `sync_state.external_task_id` is indexed (idx_sync_state_external_task_id),
    so this is O(log n) and well inside the 200ms handler SLA.
    """
    async with session_factory() as session:
        result = await session.execute(
            select(SyncState.zoho_task_id).where(
                SyncState.external_task_id == todoist_task_id
            )
        )
        return result.scalar_one_or_none()


@router.post("/todoist")
async def todoist_webhook(request: Request):
    """Todoist webhook: HMAC-verify, then dispatch by event_name.

    Security: HMAC-SHA256 over the RAW request body (Pitfall 1); constant-time
    comparison via hmac.compare_digest (Pitfall 5). Parsed body is trusted only
    after signature verification succeeds.

    Routing:
      item:added   → discard (echo of our own write or native task; both cases no-op)
      item:updated / item:completed / item:uncompleted → lookup sync_state → enqueue
      item:deleted → lookup sync_state → enqueue (worker handles delete path)
      other        → log DEBUG, return 200
    """
    raw_body = await request.body()  # MUST precede any request.json() call (Pitfall 1)

    settings = get_settings()
    expected = base64.b64encode(
        hmac.new(
            settings.todoist_client_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    received = request.headers.get("X-Todoist-Hmac-SHA256", "")
    if not hmac.compare_digest(expected, received):
        raise HTTPException(status_code=401, detail="invalid signature")

    if settings.task_provider != "todoist":
        log.info(
            "todoist_webhook_provider_inactive",
            active_provider=settings.task_provider,
        )
        return {"ok": True}

    try:
        payload = await request.json()  # safe: body already cached by Starlette
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    event_name = payload.get("event_name", "") or ""
    event_data = payload.get("event_data") or {}
    todoist_task_id = str(event_data.get("id", ""))

    # Project isolation gate (Pitfall 6). Todoist webhooks fire for ALL account
    # tasks; filter to the configured sync project at the edge to avoid
    # spurious DB lookups.
    raw_project_id = event_data.get("project_id")
    if raw_project_id is None:
        log.warning(
            "todoist_event_missing_project_id",
            event_name=event_name,
            todoist_task_id=todoist_task_id,
        )
        return {"ok": True}
    if str(raw_project_id) != str(settings.todoist_project_id):
        log.debug(
            "todoist_event_wrong_project",
            event_name=event_name,
            todoist_task_id=todoist_task_id,
            project_id=raw_project_id,
        )
        return {"ok": True}

    redis = request.app.state.redis
    session_factory = request.app.state.session_factory

    if event_name == "item:added":
        # Always discard: either a native Todoist task (not synced to Zoho in v1)
        # or an echo of our own Zoho→Todoist write (suppressed by hash-match anyway).
        log.debug("todoist_item_added_discarded", todoist_task_id=todoist_task_id)
        return {"ok": True}

    if event_name in ("item:updated", "item:completed", "item:uncompleted"):
        zoho_id = await _lookup_zoho_id(session_factory, todoist_task_id)
        if zoho_id is None:
            # Unsynced task; reconciler will catch up.
            log.warning(
                "todoist_event_no_sync_state",
                event_name=event_name,
                todoist_task_id=todoist_task_id,
            )
            return {"ok": True}
        await enqueue_sync(redis, zoho_id, defer_secs=0, source="todoist_webhook")
        return {"ok": True}

    if event_name == "item:deleted":
        zoho_id = await _lookup_zoho_id(session_factory, todoist_task_id)
        if zoho_id is None:
            log.info(
                "todoist_item_deleted_no_sync_state",
                todoist_task_id=todoist_task_id,
            )
            return {"ok": True}
        # Worker's sync_task handles the delete path via refetch (ZohoNotFoundError,
        # or explicit deletion semantics in Phase 7); we hand off the same way.
        await enqueue_sync(redis, zoho_id, defer_secs=0, source="todoist_webhook")
        return {"ok": True}

    log.debug("todoist_event_ignored", event_name=event_name)
    return {"ok": True}
