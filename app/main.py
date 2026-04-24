# app/main.py
import asyncio
import logging as _stdlib_logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.todoist.client import TodoistClient
from app.todoist.sync_manager import startup_sync
from app.zoho.client import ZohoClient
from app.zoho.state import token_state, zoho_field_cache
from app.zoho.token_manager import (
    KV_ACCESS_TOKEN_KEY,
    KV_EXPIRES_AT_KEY,
    load_token_from_kv,
    proactive_refresh_loop,
    refresh_access_token,
    upsert_kv,
)

log = get_logger(__name__)
_log = _stdlib_logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info(
        "startup",
        zoho_region=settings.zoho_region,
        todoist_task_id_field=settings.zoho_todoist_task_id_field or "NOT_SET",
        log_level=settings.log_level,
    )

    # Build async DB session factory for token persistence.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # 1. Load token from kv_store. Refresh if missing or expired.
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

    # 2. Resolve Todoist_Task_ID api_name + Status picklist via field metadata.
    client = ZohoClient(access_token=access_token)
    meta = await client.get_fields_metadata("Tasks")
    zoho_field_cache["todoist_task_id_api_name"] = meta["todoist_task_id_api_name"]
    zoho_field_cache["status_picklist_values"] = meta["status_picklist_values"]

    if not meta["todoist_task_id_api_name"]:
        _log.warning("zoho_todoist_task_id_field_not_found")
        log.warning(
            "zoho_todoist_task_id_field_not_found",
            hint="No custom field with 'Todoist' in field_label — check Zoho Tasks module settings.",
        )
    # Pitfall 5: verify configured terminal statuses exist in the live picklist.
    picklist = set(meta["status_picklist_values"])
    for status in settings.zoho_terminal_statuses_list:
        if picklist and status not in picklist:
            _log.warning("zoho_terminal_status_not_in_picklist")
            log.warning(
                "zoho_terminal_status_not_in_picklist",
                configured_status=status,
                available_status_values=sorted(picklist),
            )

    # 3. Start the proactive refresh loop as a background asyncio task.
    refresh_task = asyncio.create_task(
        proactive_refresh_loop(token_state, session_factory),
        name="zoho_proactive_refresh_loop",
    )
    app.state.zoho_refresh_task = refresh_task

    # 4. Initialise TodoistClient and run startup sync (loads/persists sync_token,
    #    filters footerless items). A failure (TodoistAuthError) propagates and
    #    halts boot — matches Zoho's fail-fast posture (SYNC-5).
    todoist_client = TodoistClient(api_token=settings.todoist_api_token)
    try:
        await startup_sync(todoist_client, session_factory, settings)
    except Exception:
        await todoist_client.close()  # prevent httpx client leak on boot failure
        raise
    app.state.todoist_client = todoist_client

    yield

    # Shutdown: cancel the refresh task.
    refresh_task.cancel()
    try:
        await refresh_task
    except (asyncio.CancelledError, Exception):
        pass
    # Close the Todoist HTTP client (frees httpx.AsyncClient).
    await app.state.todoist_client.close()
    await engine.dispose()
    log.info("shutdown")


app = FastAPI(lifespan=lifespan)
