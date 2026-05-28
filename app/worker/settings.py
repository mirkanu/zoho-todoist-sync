"""arq WorkerSettings + lifecycle hooks for the worker service.

Startup pattern mirrors app/main.py lifespan:
  1. Configure structlog, set resend.api_key.
  2. Build SQLAlchemy engine + async_sessionmaker.
  3. Load Zoho access token from kv_store; refresh if missing/expired; upsert.
  4. Publish token into shared token_state dict (app.zoho.state).
  5. Instantiate ZohoClient + TodoistClient; stash in ctx.
  6. Launch proactive_refresh_loop as a background asyncio task so the
     in-memory Zoho token stays fresh for the lifetime of the worker
     (resolves RESEARCH.md Open Q1 / Pitfall 7).

Shutdown: cancel the refresh task (awaiting with CancelledError swallowed),
close TodoistClient's httpx pool, dispose SQLAlchemy engine.

NOTE: `redis_settings` is evaluated at class definition (import time).
Tests that monkeypatch REDIS_URL must import this module AFTER the fixture
runs, or call `importlib.reload(app.worker.settings)` after cache_clear().
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import resend
from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.todoist.client import TodoistClient
from app.worker.daily_summary import daily_summary
from app.worker.jobs import sync_task
from app.worker.reconciler import orphan_sweep, reconcile_sweep, renew_zoho_webhook
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


async def on_startup(ctx: dict) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    resend.api_key = settings.resend_api_key
    log.info("worker_startup", log_level=settings.log_level)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    ctx["engine"] = engine
    ctx["session_factory"] = session_factory

    async with session_factory() as session:
        stored_token, stored_expires_at = await load_token_from_kv(session)

    now_utc = datetime.now(timezone.utc)
    # Refresh if token is missing, unknown expiry, already expired, OR within 5
    # minutes of expiry.  Without this buffer, a restart at the exact moment the
    # token is about to expire would pass the check, then fail every API call
    # until the proactive_refresh_loop fires 50 minutes later.
    needs_refresh = (
        not stored_token
        or stored_expires_at is None
        or stored_expires_at <= now_utc + timedelta(minutes=5)
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
    ctx["zoho_client"] = zoho_client
    ctx["todoist_client"] = TodoistClient(api_token=settings.todoist_api_token)

    meta = await zoho_client.get_fields_metadata("Tasks")
    zoho_field_cache["todoist_task_id_api_name"] = meta["todoist_task_id_api_name"]

    # Keep in-memory token fresh while the worker runs.
    # ZohoClient reads token_state["access_token"] by reference, so refreshes
    # propagate without needing to rebuild the client.
    ctx["_refresh_task"] = asyncio.create_task(
        proactive_refresh_loop(token_state, session_factory)
    )
    log.info("worker_startup_complete")


async def on_shutdown(ctx: dict) -> None:
    log.info("worker_shutdown")
    refresh_task = ctx.get("_refresh_task")
    if refresh_task is not None:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
        except Exception:
            # Don't let a refresh-loop error block graceful shutdown.
            log.warning("worker_refresh_task_shutdown_error", exc_info=True)
    todoist_client = ctx.get("todoist_client")
    if todoist_client is not None:
        await todoist_client.close()
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()


class WorkerSettings:
    functions = [
        func(sync_task, timeout=60, keep_result=300, max_tries=4),
    ]
    cron_jobs = [
        cron(reconcile_sweep,     minute={0, 15, 30, 45}, second=0, timeout=300),
        cron(orphan_sweep,        minute={0},             second=0, timeout=600),
        cron(daily_summary,       hour={0}, minute={0},   second=0, timeout=120),
        cron(renew_zoho_webhook,  minute={0, 45},         second=0, timeout=30),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
