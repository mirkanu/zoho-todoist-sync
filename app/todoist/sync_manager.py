"""Todoist sync_token persistence and startup sync orchestration.

SEED-7: sync_token persists across restarts so Sync API calls resume
incrementally; falls back to '*' (full sync) when missing or corrupted.

SYNC-8: items without [zoho:ID] footer are discarded at the read boundary;
deleted items are logged and skipped in Phase 3 (read-only).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import KVStore
from app.todoist.normalise import extract_zoho_id
from app.zoho.token_manager import upsert_kv  # reused — do NOT duplicate

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.todoist.client import TodoistClient

log = get_logger(__name__)

KV_SYNC_TOKEN_KEY = "todoist_sync_token"
FULL_SYNC_SENTINEL = "*"


async def load_sync_token(session: AsyncSession) -> str:
    """Load stored sync_token from kv_store. Returns '*' if missing or empty."""
    row = await session.get(KVStore, KV_SYNC_TOKEN_KEY)
    if row is None or not row.value:
        return FULL_SYNC_SENTINEL
    return row.value


async def save_sync_token(session: AsyncSession, token: str) -> None:
    """Persist sync_token to kv_store and commit.

    upsert_kv does not commit internally (Phase 2 contract). We commit here
    because this is an atomic single-key write.
    """
    await upsert_kv(session, KV_SYNC_TOKEN_KEY, token)
    await session.commit()


async def startup_sync(
    todoist_client: "TodoistClient",
    session_factory: Callable[[], Any],
    settings: "Settings",
) -> None:
    """Run the startup Todoist Sync API poll.

    Flow:
      1. Load stored sync_token (or '*' for first boot).
      2. Call fetch_sync_delta with project_id filter.
      3. Persist returned new sync_token BEFORE processing items (crash-safe
         via idempotent downstream processing).
      4. Walk items: log+skip deleted, log+discard footerless, count rest.
         (Phase 3 is read-only — processed items are counted only.)
    """
    async with session_factory() as session:
        stored_token = await load_sync_token(session)

    items, new_token = await todoist_client.fetch_sync_delta(
        sync_token=stored_token,
        project_id=settings.todoist_project_id,
    )

    # SEED-7: persist BEFORE processing so a crash mid-processing does
    # not cause re-fetch on next boot (downstream is idempotent via hash check).
    async with session_factory() as session:
        await save_sync_token(session, new_token)

    total = len(items)
    discarded_no_footer = 0
    deleted = 0
    processed = 0

    for item in items:
        if item.get("is_deleted"):
            deleted += 1
            log.info("todoist_item_deleted_skipped", todoist_id=item.get("id"))
            continue
        zoho_id = extract_zoho_id(item.get("description"))
        if zoho_id is None:
            discarded_no_footer += 1
            log.info(
                "todoist_item_no_footer_discarded",
                todoist_id=item.get("id"),
            )
            continue
        processed += 1
        # Phase 3 is read-only. Phase 5 will hand off `zoho_id` to the sync pipeline.

    log.info(
        "todoist_startup_sync_complete",
        total=total,
        processed=processed,
        discarded_no_footer=discarded_no_footer,
        deleted=deleted,
        full_sync=(stored_token == FULL_SYNC_SENTINEL),
    )
