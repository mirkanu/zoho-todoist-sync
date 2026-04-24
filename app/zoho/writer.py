"""Zoho CRM v8 write operations: update / complete / delete / write-back Todoist ID.

Mirrors app.zoho.client conventions:
- Raw httpx.AsyncClient per call (matches ZohoClient pattern; avoids stale-token caching)
- Typed exceptions imported from app.zoho.client (NOT redefined here)
- _zoho_handle maps HTTP status to typed exception; 207 dispatched to per-record status check
- Resend email notifications on delete (fire-and-forget, EDGE-6)
"""
from __future__ import annotations

from typing import Any

import httpx
import resend

from app.core.logging import get_logger
from app.core.normalise import NormalisedTask
from app.core.priority import todoist_to_zoho_priority
from app.zoho.client import (
    ZOHO_EU_BASE_URL,
    ZohoAPIError,
    ZohoAuthError,
    ZohoNotFoundError,
    ZohoRateLimitError,
)
from app.zoho.state import zoho_field_cache

log = get_logger(__name__)


def _zoho_handle(resp: httpx.Response, context: str = "") -> Any:
    """Dispatch Zoho non-2xx + 207 to typed exceptions. Returns parsed JSON on success."""
    if resp.status_code == 401:
        raise ZohoAuthError(f"401 Unauthorized - {context}")
    if resp.status_code == 404:
        raise ZohoNotFoundError(f"404 Not Found - {context}")
    if resp.status_code == 429:
        raise ZohoRateLimitError(f"429 Rate limit - {context}")
    if resp.status_code == 207:
        body = resp.json()
        record = (body.get("data") or [{}])[0]
        if record.get("status") != "success":
            raise ZohoAPIError(f"207 partial failure - {context}: {record}")
        return body
    if not (200 <= resp.status_code < 300):
        raise ZohoAPIError(f"{resp.status_code} - {context}: {resp.text[:200]}")
    # 204 No Content has no JSON body - don't force-parse
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Zoho-oauthtoken {access_token}"}


async def update_zoho_task(
    zoho_task_id: str,
    normalised: NormalisedTask,
    access_token: str,
) -> None:
    """SYNC-3 + EDGE-3: PUT synced fields. Due_Date=None serialises to JSON null (A1)."""
    payload = {
        "Subject": normalised.title,
        "Due_Date": normalised.due_date,   # "YYYY-MM-DD" or None -> JSON null
        "Priority": todoist_to_zoho_priority(normalised.priority),
    }
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_task_id}",
            headers=_auth_headers(access_token),
            json={"data": [payload]},
        )
    _zoho_handle(resp, f"PUT /Tasks/{zoho_task_id}")
    log.info("zoho_task_updated", zoho_id=zoho_task_id)


async def complete_zoho_task(zoho_task_id: str, access_token: str) -> None:
    """EDGE-4: PUT Status = first configured terminal status. Never hardcode 'Completed'."""
    from app.core.config import get_settings  # lazy import so tests can patch via monkeypatch.setenv
    statuses = get_settings().zoho_terminal_statuses_list
    if not statuses:
        raise ZohoAPIError("ZOHO_TERMINAL_STATUSES is empty — cannot complete task")
    terminal = statuses[0]
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_task_id}",
            headers=_auth_headers(access_token),
            json={"data": [{"Status": terminal}]},
        )
    _zoho_handle(resp, f"PUT /Tasks/{zoho_task_id} complete")
    log.info("zoho_task_completed", zoho_id=zoho_task_id, status=terminal)


async def delete_zoho_task(zoho_task_id: str, access_token: str) -> None:
    """EDGE-2: DELETE Zoho task + Resend email. 404 is idempotent (no email). EDGE-6: Resend failure logged."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_task_id}",
            headers=_auth_headers(access_token),
        )
    if resp.status_code == 404:
        log.info("zoho_delete_idempotent", zoho_id=zoho_task_id)
        return  # already gone - do NOT send email (Pitfall 5)
    _zoho_handle(resp, f"DELETE /Tasks/{zoho_task_id}")
    log.info("zoho_task_deleted", zoho_id=zoho_task_id)
    await _send_deletion_notification(
        subject=f"[zoho-todoist-sync] Zoho task deleted: {zoho_task_id}",
        html=f"<p>Zoho task <code>{zoho_task_id}</code> was deleted by the sync service "
             f"(propagation from a Todoist deletion).</p>",
    )


async def write_todoist_id_to_zoho(
    zoho_task_id: str,
    todoist_task_id: str,
    access_token: str,
) -> None:
    """SYNC-6: write Todoist ID into the configured Zoho custom field. Pitfall 3 guards unresolved cache."""
    field_api_name = zoho_field_cache.get("todoist_task_id_api_name")
    if not field_api_name:
        raise ZohoAPIError("todoist_task_id_api_name not resolved - cannot write ID linkage")
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_task_id}",
            headers=_auth_headers(access_token),
            json={"data": [{field_api_name: todoist_task_id}]},
        )
    _zoho_handle(resp, f"PUT /Tasks/{zoho_task_id} write_todoist_id")
    log.info("zoho_todoist_id_written", zoho_id=zoho_task_id, todoist_id=todoist_task_id)


async def _send_deletion_notification(subject: str, html: str) -> None:
    """Fire-and-forget Resend email. EDGE-6: failure logged, NOT re-raised."""
    try:
        params: resend.Emails.SendParams = {
            "from": "sync-alerts@resend.dev",  # A3: update to verified domain in Phase 8
            "to": ["manuelkuhs@gmail.com"],
            "subject": subject,
            "html": html,
        }
        await resend.Emails.send_async(params)
    except Exception as exc:
        log.error("resend_email_failed", error=str(exc))
        # Do NOT re-raise - EDGE-6.
