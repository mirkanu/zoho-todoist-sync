"""Recreate the 25 Zoho tasks for the still-active Nirvana partners.

Recovery for the 2026-10-20 orphan_sweep incident — restricted scope:

  - Skip the 49 not_found (user confirmed they were intentionally
    completed; both sides consistent).
  - Skip the 2 trash (Nirvana trash matches Zoho trash — 204 on GET).
  - For the 25 still-active Nirvana tasks: POST a fresh Zoho task with
    the same Subject, link via sync_state to the EXISTING Nirvana UUID.
    We do NOT create a new Nirvana task — the original still exists.

Inputs: recovery_inputs_2026_10_20.csv (state in {active, trash,
not_found}).

Output: sync_state rows + Resend summary (one email, not 25).
"""
from __future__ import annotations

import asyncio
import csv
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
import resend
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.hash import canonical_hash
from app.core.logging import configure_logging, get_logger
from app.core.normalise import NormalisedTask
from app.db.models import SyncState
from app.zoho.client import ZOHO_EU_BASE_URL
from app.zoho.token_manager import load_token_from_kv

log = get_logger(__name__)

INPUT_CSV = "/work/recovery_inputs_2026_10_20.csv"
OWNER_PAYLOAD = "owner_id"


def _build_zoho_payload(settings, title: str, is_completed: bool) -> dict[str, Any]:
    """Compose the POST /Tasks body. Owner is the configured sync user; status is
    'Not Started' for live tasks or the first configured terminal status if completed."""
    status = (
        settings.zoho_terminal_statuses_list[0]
        if is_completed and settings.zoho_terminal_statuses_list
        else "Not Started"
    )
    return {
        "Subject": title,
        "Status": status,
        "Owner": {"id": settings.zoho_user_id},
    }


async def _post_zoho_task(
    http: httpx.AsyncClient,
    headers: dict,
    payload: dict,
) -> str:
    """POST a single new task to Zoho, return new Zoho ID."""
    resp = await http.post(
        f"{ZOHO_EU_BASE_URL}/Tasks",
        headers=headers,
        json={"data": [payload]},
    )
    if resp.status_code not in (200, 201, 207):
        raise RuntimeError(f"Zoho POST {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    data = body.get("data") or []
    if data and data[0].get("status") == "success":
        return data[0]["details"]["id"]
    raise RuntimeError(f"Zoho POST non-success: {body}")


def _nirvana_completed(task: dict[str, Any]) -> bool:
    """nirvana_task_to_normalised semantics: completed is any non-null date."""
    return task.get("completed") is not None


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    resend.api_key = s.resend_api_key

    # Load 76-row CSV, filter to active.
    targets: list[dict[str, str]] = []
    with open(INPUT_CSV) as f:
        for row in csv.DictReader(f):
            if row["state"] == "active":
                targets.append(row)
    log.info("recover_25_targets_loaded", count=len(targets))

    # Pull Zoho token, refresh if needed.
    engine = create_async_engine(s.database_url, pool_pre_ping=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as session:
        token, _ = await load_token_from_kv(session)
    zoho_headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    http = httpx.AsyncClient(timeout=30)

    succeeded: list[tuple[str, str, str]] = []   # (new_zoho_id, nirvana_id, title)
    failed: list[tuple[str, str, str]] = []      # (old_zoho_id, nirvana_id, error)

    try:
        for row in targets:
            old_zid = row["old_zoho_id"]
            nirvana_id = row["nirvana_id"]
            title = row["title_for_recreate"] or "(no title)"

            payload = _build_zoho_payload(s, title, is_completed=False)
            try:
                new_zid = await _post_zoho_task(http, zoho_headers, payload)
            except Exception as exc:
                failed.append((old_zid, nirvana_id, f"zoho_post: {exc}"))
                log.error("recover_zoho_post_failed", nirvana_id=nirvana_id, error=str(exc))
                continue

            # Build sync_state row — hash matches what Nirvana normalise would
            # produce for an active task (no due_date stored in Nirvana;
            # priority ignored for Nirvana; is_completed=False).
            normalised = NormalisedTask(
                title=title,
                due_date=None,
                priority=1,
                is_completed=False,
            )
            new_hash = canonical_hash(normalised)
            now = datetime.now(timezone.utc)
            async with sf() as session:
                async with session.begin():
                    session.add(
                        SyncState(
                            zoho_task_id=new_zid,
                            external_task_id=nirvana_id,
                            provider="nirvana",
                            last_hash=new_hash,
                            last_synced_at=now,
                            zoho_last_seen=now,
                            orphan_check_count=0,
                        )
                    )
            succeeded.append((new_zid, nirvana_id, title))
            log.info(
                "recover_pair_done",
                old_zoho_id=old_zid,
                new_zoho_id=new_zid,
                nirvana_id=nirvana_id,
            )
    finally:
        await http.aclose()
        await engine.dispose()

    # Summary email.
    lines = [
        "Zoho-Todoist-Sync recovery (2026-10-20, second pass — 25 active Nirvana):",
        f"  Recreated Zoho + linked to existing Nirvana: {len(succeeded)}/25",
        f"  Failed: {len(failed)}",
        "",
        "ROOT CAUSE: orphan_sweep deleted 76 Zoho tasks because NirvanaClient's",
        "API parser was broken (wrong response key + offset/limit pagination).",
        "It had been silently returning 0 tasks / 404s. Two-cycle confirmation",
        "turned lies into deletions. The 49 'not_found' Nirvana tasks were",
        "intentionally completed by the user and stay deleted (consistent",
        "both sides). The 2 in trash are consistent (Zoho also returns 204",
        "for them, indicating trash). The 25 active Nirvana partners got",
        "their Zoho counterparts restored here.",
        "",
        "STATE:",
        "  - 49 'not_found' in Nirvana: intentionally completed — left as-is.",
        "  - 2 in Nirvana trash: also deleted in Zoho — consistent.",
        "  - 25 active in Nirvana: restored with new Zoho ID + re-linked",
        "    to original Nirvana UUID (no duplicate Nirvana tasks).",
        "  - Orphan sweep kill-switch: ON (ZTS_DISABLE_ORPHAN_SWEEP=1)",
        "    until NirvanaClient is fixed.",
    ]
    if succeeded:
        lines.append("")
        lines.append("RECOVERED (new_zoho_id, nirvana_id, title):")
        for z, n, t in succeeded:
            lines.append(f"  {z} <- {n}  {t[:60]}")
    if failed:
        lines.append("")
        lines.append("FAILED:")
        for o, n, err in failed:
            lines.append(f"  old={o} nirvana={n} -> {err[:100]}")

    body = "\n".join(lines)
    try:
        resend.Emails.send(
            {
                "from": s.resend_sender_email,
                "to": ["manuelkuhs@gmail.com"],
                "subject": f"[zoho-todoist-sync] Recovery: {len(succeeded)}/25 restored",
                "text": body,
            }
        )
        log.info("recover_summary_emailed")
    except Exception as exc:
        log.error("recover_summary_email_failed", error=str(exc))

    # Also save the summary to a file for posterity + push to Telegram.
    summary_path = "/work/recovery_2026_10_20_summary.txt"
    with open(summary_path, "w") as f:
        f.write(body)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "http://localhost:4820/api/services/telegram/send-file",
                json={"filePath": summary_path, "caption": f"25-task recovery: {len(succeeded)}/25"},
            )
        log.info("recover_summary_telegram", status=r.status_code)
    except Exception as exc:
        log.warning("recover_summary_telegram_failed", error=str(exc))

    print(body)


if __name__ == "__main__":
    asyncio.run(main())
