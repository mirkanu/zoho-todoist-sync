"""End-to-end live sync verification — manual gate before migration.

Usage:
    python scripts/e2e_test.py

Per D-04: NOT part of pytest suite. Per SEED-4: must complete before migrate.py runs against live data.
Per D-05: creates a real Zoho task at start, runs assertions, deletes from both systems at end.
Per D-06: polls Todoist every 5s for up to 90s for each propagation step.
Per Pitfall 4: try/finally ensures cleanup even on assertion failure.
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from dotenv import load_dotenv
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import SyncEvent
from app.todoist.client import TodoistClient
from app.worker.enqueue import enqueue_sync
from app.zoho.client import ZohoClient, ZOHO_EU_BASE_URL
from app.zoho.state import token_state, zoho_field_cache
from app.zoho.token_manager import (
    KV_ACCESS_TOKEN_KEY, KV_EXPIRES_AT_KEY,
    load_token_from_kv, refresh_access_token, upsert_kv,
)
from app.zoho.writer import (
    _auth_headers,
    complete_zoho_task,
    delete_zoho_task,
)

log = get_logger(__name__)

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 90
MAX_SYNC_EVENTS_FOR_TEST = 20  # anti-loop assertion threshold


async def create_zoho_test_task(subject: str, owner_id: str, access_token: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ZOHO_EU_BASE_URL}/Tasks",
            headers=_auth_headers(access_token),
            json={"data": [{"Subject": subject, "Owner": {"id": owner_id}, "Priority": "Normal"}]},
        )
    resp.raise_for_status()
    return str(resp.json()["data"][0]["details"]["id"])


async def find_todoist_task_for_zoho(todoist_client: TodoistClient, project_id: str, zoho_task_id: str):
    """Return the Todoist task whose description contains [zoho:{zoho_task_id}], or None."""
    marker = f"[zoho:{zoho_task_id}]"
    tasks = await todoist_client._api.get_tasks(project_id=project_id)
    async for page in tasks:
        for t in page:
            if marker in (t.description or ""):
                return t
    return None


async def poll_until(predicate_coro_factory, *, timeout_s: int = POLL_TIMEOUT_S, interval_s: int = POLL_INTERVAL_S, error_msg: str):
    """Poll predicate until it returns truthy or timeout. Returns the truthy value."""
    deadline = time.monotonic() + timeout_s
    last_value = None
    while time.monotonic() < deadline:
        last_value = await predicate_coro_factory()
        if last_value:
            return last_value
        await asyncio.sleep(interval_s)
    raise AssertionError(f"{error_msg} (after {timeout_s}s, last_value={last_value!r})")


async def _check_todoist_field(todoist_client, project_id, zoho_id, field, expected):
    t = await find_todoist_task_for_zoho(todoist_client, project_id, zoho_id)
    if t is None:
        return None
    actual = getattr(t, field, None)
    return t if actual == expected else None


async def _check_todoist_due(todoist_client, project_id, zoho_id, expected_date):
    t = await find_todoist_task_for_zoho(todoist_client, project_id, zoho_id)
    if t is None or t.due is None:
        return None
    # t.due may be an object with .date attribute or a dict
    date_val = getattr(t.due, "date", None) or (t.due.get("date") if isinstance(t.due, dict) else None)
    return t if date_val == expected_date else None


async def _check_todoist_completed(todoist_client, project_id, zoho_id, todoist_id):
    # Active list no longer contains it — consider that proof of completion.
    t = await find_todoist_task_for_zoho(todoist_client, project_id, zoho_id)
    return True if t is None else None


async def run_e2e(
    access_token: str,
    owner_id: str,
    todoist_client: TodoistClient,
    project_id: str,
    session_factory,
    redis,
) -> None:
    ts = int(time.time())
    subject = f"E2E test {ts}"
    zoho_id: str | None = None
    todoist_id: str | None = None

    try:
        # 1. Create Zoho task (D-05)
        print(f"[E2E] Creating Zoho task subject={subject!r} ...")
        zoho_id = await create_zoho_test_task(subject, owner_id, access_token)
        print(f"[E2E] Zoho task created: {zoho_id}")

        # 2. Enqueue sync directly (Zoho suppresses webhooks for same-app API changes)
        # Use no _job_id so E2E steps don't dedup each other (keep_result=300s would block)
        print(f"[E2E] Enqueueing sync job for {zoho_id} ...")
        await redis.enqueue_job("sync_task", zoho_id, _defer_by=2)

        # 3. Wait for Todoist counterpart (D-06)
        print(f"[E2E] Waiting up to {POLL_TIMEOUT_S}s for Todoist propagation ...")
        t = await poll_until(
            lambda: find_todoist_task_for_zoho(todoist_client, project_id, zoho_id),
            error_msg=f"Todoist task with [zoho:{zoho_id}] never appeared",
        )
        todoist_id = t.id
        assert subject in t.content, f"Title mismatch: expected {subject!r} in {t.content!r}"
        print(f"[E2E] Todoist task linked: {todoist_id}")

        # 4. Edit subject in Zoho → verify Todoist update
        new_subject = f"{subject} (edited)"
        print(f"[E2E] Editing Zoho subject to {new_subject!r} ...")
        async with httpx.AsyncClient() as client:
            await client.put(f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_id}",
                headers=_auth_headers(access_token),
                json={"data": [{"Subject": new_subject}]})
        await redis.enqueue_job("sync_task", zoho_id, _defer_by=2)
        await poll_until(
            lambda: _check_todoist_field(todoist_client, project_id, zoho_id, "content", new_subject),
            error_msg="Todoist content did not update after Zoho subject edit",
        )
        print("[E2E] Subject propagation verified")

        # 5. Edit due_date in Zoho → verify Todoist due
        new_due = "2026-12-31"
        print(f"[E2E] Editing Zoho Due_Date to {new_due} ...")
        async with httpx.AsyncClient() as client:
            await client.put(f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_id}",
                headers=_auth_headers(access_token),
                json={"data": [{"Due_Date": new_due}]})
        await redis.enqueue_job("sync_task", zoho_id, _defer_by=2)
        await poll_until(
            lambda: _check_todoist_due(todoist_client, project_id, zoho_id, new_due),
            error_msg="Todoist due.date did not match after Zoho Due_Date edit",
        )
        print("[E2E] Due date propagation verified")

        # 6. Edit priority in Zoho → verify Todoist priority=4 (Highest)
        print("[E2E] Editing Zoho Priority to Highest ...")
        async with httpx.AsyncClient() as client:
            await client.put(f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_id}",
                headers=_auth_headers(access_token),
                json={"data": [{"Priority": "Highest"}]})
        await redis.enqueue_job("sync_task", zoho_id, _defer_by=2)
        await poll_until(
            lambda: _check_todoist_field(todoist_client, project_id, zoho_id, "priority", 4),
            error_msg="Todoist priority did not update to 4 after Zoho Priority=Highest",
        )
        print("[E2E] Priority propagation verified")

        # 7. Complete Zoho task → verify Todoist task closed
        print("[E2E] Completing Zoho task ...")
        await complete_zoho_task(zoho_id, access_token)
        await redis.enqueue_job("sync_task", zoho_id, _defer_by=2)
        await poll_until(
            lambda: _check_todoist_completed(todoist_client, project_id, zoho_id, todoist_id),
            error_msg="Todoist task was not closed after Zoho completion",
        )
        print("[E2E] Completion propagation verified")

        # 7. Anti-loop assertion on sync_events
        async with session_factory() as session:
            count = (await session.execute(
                select(func.count()).select_from(SyncEvent).where(SyncEvent.zoho_task_id == zoho_id)
            )).scalar_one()
        print(f"[E2E] sync_events count for test task: {count}")
        assert count <= MAX_SYNC_EVENTS_FOR_TEST, (
            f"Possible infinite loop: {count} sync_events for {zoho_id} (threshold {MAX_SYNC_EVENTS_FOR_TEST})"
        )

        print("[E2E] PASS — full sync round-trip succeeded")

    finally:
        # Pitfall 4: ALWAYS clean up, even on failure
        print("[E2E] Cleanup phase ...")
        if todoist_id:
            try:
                await todoist_client._api.delete_task(todoist_id)
                print(f"[E2E] Deleted Todoist task {todoist_id}")
            except Exception as e:
                print(f"[E2E] WARN: failed to delete Todoist task {todoist_id}: {e}")
        if zoho_id:
            try:
                await delete_zoho_task(zoho_id, access_token)
                print(f"[E2E] Deleted Zoho task {zoho_id}")
            except Exception as e:
                print(f"[E2E] WARN: failed to delete Zoho task {zoho_id}: {e}")


async def main() -> int:
    load_dotenv()
    get_settings.cache_clear()
    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        stored_token, stored_expires_at = await load_token_from_kv(session)
    now_utc = datetime.now(timezone.utc)
    if not stored_token or stored_expires_at is None or stored_expires_at <= now_utc:
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

    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    todoist_client = TodoistClient(api_token=settings.todoist_api_token)
    try:
        await run_e2e(
            access_token=access_token,
            owner_id=settings.zoho_user_id,
            todoist_client=todoist_client,
            project_id=settings.todoist_project_id,
            session_factory=session_factory,
            redis=redis,
        )
        return 0
    except AssertionError as e:
        print(f"[E2E] FAIL — {e}", file=sys.stderr)
        return 1
    finally:
        await todoist_client.close()
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
