"""One-shot migration: link existing Make.com Zoho<->Todoist task pairs into sync_state.

Usage:
    python scripts/migrate.py [--dry-run]

Idempotent: safe to re-run. Tasks already in sync_state are skipped.
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
from app.todoist.client import TodoistClient, TodoistNotFoundError
from app.todoist.writer import create_todoist_task
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


async def fetch_all_open_zoho_tasks(access_token: str, owner_id: str) -> list[dict]:
    """Fetch all non-Completed Zoho tasks for owner_id, paginated.

    Uses Zoho v8 search criteria: ((Status:not_equal:Completed)and(Owner:equals:{owner_id})).
    If the criteria syntax differs in this org, fall back to fetching all tasks and filtering
    client-side.
    """
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


async def migrate_one_task(
    record: dict,
    zoho_field_api_name: str,
    terminal_statuses: list[str],
    access_token: str,
    todoist_client: TodoistClient,
    session_factory,
    counters: dict[str, int],
    dry_run: bool,
) -> None:
    """Link or create a single Zoho<->Todoist pair. Idempotent: skips if sync_state exists."""
    zoho_task_id = str(record["id"])

    # Idempotency guard (Pitfall 3): skip if already linked
    async with session_factory() as session:
        existing = (await session.execute(
            select(SyncState).where(SyncState.zoho_task_id == zoho_task_id)
        )).scalar_one_or_none()
    if existing is not None:
        counters["already_linked"] += 1
        log.info("migration_skip_already_linked", zoho_task_id=zoho_task_id)
        return

    normalised = zoho_record_to_normalised(record, terminal_statuses)
    last_hash = canonical_hash(normalised)
    todoist_id = record.get(zoho_field_api_name) or None

    if todoist_id:
        try:
            await todoist_client.fetch_todoist_task(str(todoist_id))
        except TodoistNotFoundError:
            log.warning(
                "migration_todoist_404_fallback",
                zoho_task_id=zoho_task_id,
                stale_todoist_id=todoist_id,
            )
            if not dry_run:
                new_id = await create_todoist_task(normalised, zoho_task_id, todoist_client._api)
                await write_todoist_id_to_zoho(zoho_task_id, new_id, access_token)
                await _upsert_sync_state(session_factory, zoho_task_id, new_id, last_hash)
            counters["recreated"] += 1
            return

        if not dry_run:
            await _upsert_sync_state(session_factory, zoho_task_id, str(todoist_id), last_hash)
        counters["linked"] += 1
        return

    # No Todoist_Task_ID: create fresh, write back, store sync_state
    if not dry_run:
        new_id = await create_todoist_task(normalised, zoho_task_id, todoist_client._api)
        await write_todoist_id_to_zoho(zoho_task_id, new_id, access_token)
        await _upsert_sync_state(session_factory, zoho_task_id, new_id, last_hash)
    counters["created"] += 1


async def _upsert_sync_state(
    session_factory,
    zoho_task_id: str,
    todoist_task_id: str,
    last_hash: str,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        row = SyncState(
            zoho_task_id=zoho_task_id,
            todoist_task_id=todoist_task_id,
            last_hash=last_hash,
            last_synced_at=now,
        )
        session.add(row)
        await session.commit()


async def run_migration(
    records: list[dict],
    zoho_field_api_name: str,
    terminal_statuses: list[str],
    access_token: str,
    todoist_client: TodoistClient,
    session_factory,
    dry_run: bool,
) -> dict[str, int]:
    counters = {"already_linked": 0, "linked": 0, "created": 0, "recreated": 0, "errors": 0}
    for record in records:
        try:
            await migrate_one_task(
                record, zoho_field_api_name, terminal_statuses,
                access_token, todoist_client, session_factory, counters, dry_run,
            )
        except Exception as e:
            counters["errors"] += 1
            log.error(
                "migration_record_failed",
                zoho_task_id=record.get("id"),
                error=str(e),
                exc_info=True,
            )
    prefix = "DRY-RUN" if dry_run else "MIGRATED"
    print(
        f"{prefix}: already_linked={counters['already_linked']}, "
        f"linked={counters['linked']}, created={counters['created']}, "
        f"recreated={counters['recreated']}, errors={counters['errors']}"
    )
    return counters


async def main(dry_run: bool) -> int:
    load_dotenv()
    get_settings.cache_clear()  # Pitfall 2: MUST clear after load_dotenv
    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Token bootstrap (mirrors app/worker/settings.py:on_startup)
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
    zoho_field_api_name = meta["todoist_task_id_api_name"]
    zoho_field_cache["todoist_task_id_api_name"] = zoho_field_api_name

    todoist_client = TodoistClient(api_token=settings.todoist_api_token)

    try:
        records = await fetch_all_open_zoho_tasks(access_token, settings.zoho_user_id)
        log.info("migration_fetched", count=len(records), dry_run=dry_run)
        await run_migration(
            records=records,
            zoho_field_api_name=zoho_field_api_name,
            terminal_statuses=settings.zoho_terminal_statuses_list,
            access_token=access_token,
            todoist_client=todoist_client,
            session_factory=session_factory,
            dry_run=dry_run,
        )
    finally:
        await todoist_client.close()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate existing Make.com Zoho<->Todoist pairs into sync_state."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts; do not write to Todoist, Zoho, or DB.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(dry_run=args.dry_run)))
