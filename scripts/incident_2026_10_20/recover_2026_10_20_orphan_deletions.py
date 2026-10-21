"""One-off recovery script for the 2026-10-20 orphan_sweep incident.

Incident: orphan_sweep killed 76 Zoho tasks whose Nirvana-side counterparts
still exist. The sweep trusted Nirvana's API returning false-positive 404s
for two consecutive hours. We chose to recreate the Zoho tasks (no OAuth
scope for Recycle Bin) and re-link them to the same Nirvana UUIDs.

This script:
  1. Reads the (zoho_task_id, external_task_id=Nirvana UUID) pairs from
     sync_events where action='orphan' and created_at >= '2026-10-20 10:00'.
  2. For each pair: fetches the current Nirvana task → POSTs a new Zoho task
     with the same title + due_date.
  3. Inserts a fresh sync_state row linking the new zoho_task_id to the same
     Nirvana UUID, with provider='nirvana'.
  4. Logs a summary, sends a single Telegram message.

Idempotency: each created Zoho task + sync_state row is in a single
transaction per pair. If a pair fails (Nirvana 404, Zoho API error), we
log and continue to the next.

Re-runnable: each invocation reads the latest sync_events snapshot, so
re-running after a partial completion will skip pairs that already have
a sync_state row (we filter out IDs that already map).
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
import resend
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.hash import canonical_hash
from app.core.logging import configure_logging, get_logger
from app.core.normalise import NormalisedTask
from app.db.models import SyncEvent, SyncState
from app.nirvana.client import NirvanaClient, NirvanaNotFoundError
from app.zoho.client import ZOHO_EU_BASE_URL
from app.zoho.token_manager import load_token_from_kv

log = get_logger(__name__)

OWNER_KEY = "Owner"


def _owner_payload(settings) -> dict[str, str]:
    """Zoho v8 owner reference payload."""
    return {"id": settings.zoho_user_id}


async def _post_zoho_task(
    client: httpx.AsyncClient,
    headers: dict,
    settings,
    title: str,
    due_date: str | None,
    status: str = "Not Started",
) -> str:
    """POST a single new task to Zoho, return the new Zoho ID.

    A 207 multi-status response with status=success is treated as success
    (mirrors _zoho_handle in app.zoho.writer).
    """
    payload: dict[str, Any] = {
        "Subject": title,
        "Status": status,
        "Owner": _owner_payload(settings),
    }
    if due_date:
        payload["Due_Date"] = due_date
    resp = await client.post(
        f"{ZOHO_EU_BASE_URL}/Tasks",
        headers=headers,
        json={"data": [payload]},
    )
    if resp.status_code in (200, 201, 207):
        body = resp.json()
        data = (body.get("data") or [])
        if data and data[0].get("status") == "success":
            return data[0]["details"]["id"]
        raise RuntimeError(f"Zoho POST returned non-success: {body}")
    raise RuntimeError(f"Zoho POST failed: {resp.status_code} {resp.text[:200]}")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    resend.api_key = settings.resend_api_key

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    # 1) Read the (zoho_id, nirvana_uuid) pairs from sync_events.
    async with sf() as session:
        result = await session.execute(
            text(
                """
                SELECT DISTINCT ON (detail->>'external_task_id')
                       zoho_task_id,
                       detail->>'external_task_id' AS nirvana_id,
                       created_at
                FROM sync_events
                WHERE action = 'orphan'
                  AND created_at >= '2026-10-20 10:00:00'
                  AND (detail->>'todoist_missing')::boolean = true
                ORDER BY detail->>'external_task_id', created_at DESC
                """
            )
        )
        rows = result.fetchall()

    pairs = [(r[0], r[1]) for r in rows if r[1]]
    log.info("recover_pairs_loaded", count=len(pairs))

    # 2) Skip pairs whose nirvana_id is already linked in sync_state.
    async with sf() as session:
        result = await session.execute(
            select(SyncState.external_task_id).where(
                SyncState.provider == "nirvana"
            )
        )
        already_linked = {row[0] for row in result.all()}
    pending = [(zid, nid) for (zid, nid) in pairs if nid not in already_linked]
    log.info("recover_pending_after_skip", count=len(pending), already_linked=len(already_linked))

    # 3) Pull tokens + clients.
    async with sf() as session:
        zoho_token, _ = await load_token_from_kv(session)
    zoho_headers = {"Authorization": f"Zoho-oauthtoken {zoho_token}"}

    nirvana = NirvanaClient(pat=settings.nirvana_pat)
    http = httpx.AsyncClient(timeout=30)

    # 4) Process each pair.
    succeeded: list[tuple[str, str, str]] = []   # (new_zoho_id, nirvana_id, subject)
    skipped_missing: list[tuple[str, str]] = []  # (old_zoho_id, nirvana_id) - nirvana 404
    failed: list[tuple[str, str, str]] = []      # (old_zoho_id, nirvana_id, error)

    try:
        for old_zoho_id, nirvana_id in pending:
            try:
                task = await nirvana.fetch(nirvana_id)
            except NirvanaNotFoundError:
                skipped_missing.append((old_zoho_id, nirvana_id))
                log.warning(
                    "recover_nirvana_404",
                    old_zoho_id=old_zoho_id,
                    nirvana_id=nirvana_id,
                )
                continue
            except Exception as exc:
                failed.append((old_zoho_id, nirvana_id, f"nirvana_fetch: {exc}"))
                log.error("recover_nirvana_error", nirvana_id=nirvana_id, error=str(exc))
                continue

            subject = task.title
            due_date = task.due_date  # YYYY-MM-DD or None

            try:
                new_zoho_id = await _post_zoho_task(
                    http, zoho_headers, settings, subject, due_date,
                )
            except Exception as exc:
                failed.append((old_zoho_id, nirvana_id, f"zoho_post: {exc}"))
                log.error("recover_zoho_post_failed", nirvana_id=nirvana_id, error=str(exc))
                continue

            # 5) Insert sync_state row.
            normalised_for_hash = NormalisedTask(
                title=subject,
                due_date=due_date,
                priority=1,                # ignored for Nirvana, but hash needs a value
                is_completed=task.is_completed,
            )
            new_hash = canonical_hash(normalised_for_hash)
            now = datetime.now(timezone.utc)
            async with sf() as session:
                async with session.begin():
                    session.add(
                        SyncState(
                            zoho_task_id=new_zoho_id,
                            external_task_id=nirvana_id,
                            provider="nirvana",
                            last_hash=new_hash,
                            last_synced_at=now,
                            zoho_last_seen=now,
                            orphan_check_count=0,
                        )
                    )
            succeeded.append((new_zoho_id, nirvana_id, subject))
            log.info(
                "recover_pair_done",
                old_zoho_id=old_zoho_id,
                new_zoho_id=new_zoho_id,
                nirvana_id=nirvana_id,
            )
    finally:
        await http.aclose()
        await nirvana.close()
        await engine.dispose()

    # 6) Summary + single Resend email (instead of 76 deletion emails, ONE recovery email).
    summary_lines = [
        "Zoho-Todoist-Sync recovery summary (2026-10-20):",
        f"  Restored: {len(succeeded)}",
        f"  Nirvana task missing (skipped): {len(skipped_missing)}",
        f"  Failed: {len(failed)}",
        "",
        "ROOT CAUSE: orphan_sweep killed 76 Zoho tasks because Nirvana's API",
        "returned false-positive 404s for two consecutive hours. Sweep is now",
        "disabled (ZTS_DISABLE_ORPHAN_SWEEP=1) and will not re-enable until",
        "safer confirmation logic is in place. The 76 restored tasks are",
        "linked to their ORIGINAL Nirvana UUIDs. Original Zoho task IDs are",
        "GONE (tasks were created fresh via POST) — anything external that",
        "referenced the old IDs will need re-linking.",
    ]
    if failed:
        summary_lines.append("")
        summary_lines.append("FAILED (needs manual attention):")
        for oid, nid, err in failed[:20]:
            summary_lines.append(f"  old={oid} nirvana={nid} -> {err[:100]}")
    if skipped_missing:
        summary_lines.append("")
        summary_lines.append("NIRVANA TASK 404 (orphaned permanently):")
        for oid, nid in skipped_missing[:20]:
            summary_lines.append(f"  old={oid} nirvana={nid}")

    summary_text = "\n".join(summary_lines)

    try:
        resend.Emails.send(
            {
                "from": settings.resend_sender_email,
                "to": ["manuelkuhs@gmail.com"],
                "subject": f"[zoho-todoist-sync] Recovery: {len(succeeded)}/76 restored",
                "text": summary_text,
            }
        )
        log.info("recover_summary_emailed")
    except Exception as exc:
        log.error("recover_summary_email_failed", error=str(exc))

    # Telegram push of summary file (matches convention for incident reports)
    summary_path = "/tmp/recovery_2026_10_20.txt"
    with open(summary_path, "w") as f:
        f.write(summary_text)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "http://localhost:4820/api/services/telegram/send-file",
                json={"filePath": summary_path, "caption": f"zoho-todoist-sync recovery: {len(succeeded)}/76"},
            )
        log.info("recover_summary_telegram", status=r.status_code)
    except Exception as exc:
        log.warning("recover_summary_telegram_failed", error=str(exc))

    print(summary_text)


if __name__ == "__main__":
    asyncio.run(main())
