"""One-shot migration: create Nirvana tasks for currently-open Zoho tasks that
are still linked to Todoist, translating historical Todoist labels into their
Nirvana equivalents (2026-07-28 decisions).

Scope: only Zoho tasks with Status != Completed (open) that currently have a
sync_state row with provider='todoist'. Tasks with no sync_state row, or whose
sync_state.provider is already 'nirvana', are skipped (idempotent — safe to
re-run).

Label -> Nirvana translation:
  - (always)              -> tags "Expo" + "Zoho" (BASE_TAGS in app.nirvana.writer)
  - "Deferred"             -> NOT a tag; state="scheduled" (has due date) or
                               "someday" (no due date) instead of the normal "inbox"
  - "WaitingFor", "Agenda"  -> tag "Other Contacts" (existing Contacts-type tag)
  - "EPACore"               -> tag "EPACore" (auto-created; may need manual
                               reclassification to Contacts-type in the Nirvana
                               app afterward -- tag type isn't API-settable)
  - anything else           -> tag with the identical name (auto-created if new)

The original Todoist task is left untouched (not deleted) -- purely a fallback
reference. sync_state is updated IN PLACE (same zoho_task_id row) so no
duplicate task is ever created once TASK_PROVIDER is flipped to "nirvana".

Usage:
    python scripts/migrate_todoist_labels_to_nirvana.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.hash import canonical_hash
from app.core.logging import configure_logging, get_logger
from app.db.models import SyncState
from app.nirvana.client import NirvanaClient
from app.nirvana.description import build_task_note
from app.nirvana.writer import create_nirvana_task
from app.todoist.client import TodoistClient, TodoistNotFoundError
from app.zoho.client import ZohoClient, ZOHO_EU_BASE_URL
from app.zoho.normalise import zoho_record_to_normalised
from app.zoho.state import token_state, zoho_field_cache
from app.zoho.token_manager import (
    KV_ACCESS_TOKEN_KEY,
    KV_EXPIRES_AT_KEY,
    load_token_from_kv,
    refresh_access_token,
    upsert_kv,
)
from app.zoho.writer import write_todoist_id_to_zoho

log = get_logger(__name__)

DEFERRED_LABEL = "Deferred"
CONTACT_LABELS = {"WaitingFor", "Agenda"}
CONTACT_TAG_TARGET = "Other Contacts"


async def fetch_all_open_zoho_tasks(access_token: str, owner_id: str) -> list[dict]:
    """Fetch all non-Completed Zoho tasks for owner_id, paginated."""
    criteria = f"((Status:not_equal:Completed)and(Owner:equals:{owner_id}))"
    results: list[dict] = []
    page = 1
    while True:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{ZOHO_EU_BASE_URL}/Tasks/search",
                params={"criteria": criteria, "page": page, "per_page": 200},
                headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
            )
        if resp.status_code == 204:
            break
        resp.raise_for_status()
        body = resp.json()
        results.extend(body.get("data", []))
        if not body.get("info", {}).get("more_records"):
            break
        page += 1
    return results


def compute_state_and_extra_tags(labels: list[str], due_date: str | None) -> tuple[str, list[str]]:
    """Translate Todoist labels into a Nirvana initial state + extra tags,
    per the 2026-07-28 mapping decision. "Deferred" becomes a state, never a
    tag; "WaitingFor"/"Agenda" both collapse into the single "Other Contacts"
    tag; everything else passes through as an identically-named tag."""
    state = "inbox"
    extra_tags: list[str] = []
    for label in labels:
        if label == DEFERRED_LABEL:
            state = "scheduled" if due_date else "someday"
        elif label in CONTACT_LABELS:
            if CONTACT_TAG_TARGET not in extra_tags:
                extra_tags.append(CONTACT_TAG_TARGET)
        else:
            if label not in extra_tags:
                extra_tags.append(label)
    return state, extra_tags


async def migrate_one_task(
    record: dict,
    terminal_statuses: list[str],
    access_token: str,
    todoist_client: TodoistClient,
    nirvana_client: NirvanaClient,
    session_factory,
    counters: dict[str, int],
    dry_run: bool,
) -> None:
    zoho_task_id = str(record["id"])

    async with session_factory() as session:
        state_row = (await session.execute(
            select(SyncState).where(SyncState.zoho_task_id == zoho_task_id)
        )).scalar_one_or_none()

    if state_row is None:
        counters["skipped_no_sync_state"] += 1
        log.info("migration_skip_no_sync_state", zoho_task_id=zoho_task_id)
        return
    if state_row.provider == "nirvana":
        counters["skipped_already_nirvana"] += 1
        log.info("migration_skip_already_nirvana", zoho_task_id=zoho_task_id)
        return

    todoist_task_id = state_row.external_task_id
    try:
        todoist_task = await todoist_client.fetch_todoist_task(todoist_task_id)
        labels = list(todoist_task.labels or [])
    except TodoistNotFoundError:
        log.warning(
            "migration_todoist_404_no_labels",
            zoho_task_id=zoho_task_id,
            stale_todoist_id=todoist_task_id,
        )
        labels = []

    normalised = zoho_record_to_normalised(record, terminal_statuses)
    nirvana_state, extra_tags = compute_state_and_extra_tags(labels, normalised.due_date)
    note = build_task_note(zoho_task_id, record)

    log.info(
        "migration_plan",
        zoho_task_id=zoho_task_id,
        todoist_labels=labels,
        nirvana_state=nirvana_state,
        extra_tags=extra_tags,
        dry_run=dry_run,
    )

    if dry_run:
        counters["would_migrate"] += 1
        return

    new_nirvana_id = await create_nirvana_task(
        normalised, zoho_task_id, nirvana_client,
        note=note, state=nirvana_state, extra_tags=extra_tags,
    )
    await write_todoist_id_to_zoho(zoho_task_id, new_nirvana_id, access_token)

    last_hash = canonical_hash(normalised)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        row = (await session.execute(
            select(SyncState).where(SyncState.zoho_task_id == zoho_task_id).with_for_update()
        )).scalar_one()
        row.external_task_id = new_nirvana_id
        row.provider = "nirvana"
        row.last_hash = last_hash
        row.last_synced_at = now
        await session.commit()

    counters["migrated"] += 1
    log.info("migration_task_migrated", zoho_task_id=zoho_task_id, nirvana_id=new_nirvana_id)


async def run_migration(
    records: list[dict],
    terminal_statuses: list[str],
    access_token: str,
    todoist_client: TodoistClient,
    nirvana_client: NirvanaClient,
    session_factory,
    dry_run: bool,
) -> dict[str, int]:
    counters = {
        "migrated": 0, "would_migrate": 0,
        "skipped_no_sync_state": 0, "skipped_already_nirvana": 0,
        "errors": 0,
    }
    for record in records:
        try:
            await migrate_one_task(
                record, terminal_statuses, access_token,
                todoist_client, nirvana_client, session_factory, counters, dry_run,
            )
        except Exception as exc:
            counters["errors"] += 1
            log.error(
                "migration_record_failed",
                zoho_task_id=record.get("id"),
                error=str(exc),
                exc_info=True,
            )
        await asyncio.sleep(0.5)  # ~2 req/sec across Zoho/Todoist/Nirvana — safe rate

    prefix = "DRY-RUN" if dry_run else "MIGRATED"
    print(
        f"{prefix}: migrated={counters['migrated']}, would_migrate={counters['would_migrate']}, "
        f"skipped_no_sync_state={counters['skipped_no_sync_state']}, "
        f"skipped_already_nirvana={counters['skipped_already_nirvana']}, "
        f"errors={counters['errors']}"
    )
    return counters


async def main(dry_run: bool) -> int:
    load_dotenv()
    get_settings.cache_clear()  # Pitfall 2: MUST clear after load_dotenv
    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    todoist_client = None
    nirvana_client = None
    try:
        # Token bootstrap (mirrors app/worker/settings.py:on_startup / scripts/migrate.py)
        async with session_factory() as session:
            stored_token, stored_expires_at = await load_token_from_kv(session)
        now_utc = datetime.now(timezone.utc)
        needs_refresh = (
            not stored_token
            or stored_expires_at is None
            or stored_expires_at <= now_utc
        )
        if needs_refresh:
            access_token, expires_at = await refresh_access_token(settings)
            async with session_factory() as session:
                await upsert_kv(session, KV_ACCESS_TOKEN_KEY, access_token)
                await upsert_kv(session, KV_EXPIRES_AT_KEY, expires_at.isoformat())
                await session.commit()
        else:
            access_token = stored_token
            expires_at = stored_expires_at

        token_state["access_token"] = access_token
        token_state["expires_at"] = expires_at

        zoho_client = ZohoClient(access_token=access_token)
        meta = await zoho_client.get_fields_metadata("Tasks")
        zoho_field_cache["todoist_task_id_api_name"] = meta["todoist_task_id_api_name"]

        todoist_client = TodoistClient(api_token=settings.todoist_api_token)
        nirvana_client = NirvanaClient(pat=settings.nirvana_pat)

        records = await fetch_all_open_zoho_tasks(access_token, settings.zoho_user_id)
        log.info("migration_fetched", count=len(records), dry_run=dry_run)

        counters = await run_migration(
            records=records,
            terminal_statuses=settings.zoho_terminal_statuses_list,
            access_token=access_token,
            todoist_client=todoist_client,
            nirvana_client=nirvana_client,
            session_factory=session_factory,
            dry_run=dry_run,
        )
    finally:
        if todoist_client is not None:
            await todoist_client.close()
        if nirvana_client is not None:
            await nirvana_client.close()
        await engine.dispose()
    return 1 if counters["errors"] > 0 else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate open Todoist-linked Zoho tasks to Nirvana, translating labels."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the migration plan; do not write to Nirvana, Zoho, or the DB.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(dry_run=args.dry_run)))
