# Phase 8: Observability & Migration - Research

**Researched:** 2026-04-25
**Domain:** FastAPI health endpoint, arq cron jobs, SQLAlchemy count queries, migration scripting, E2E integration testing
**Confidence:** HIGH

## Summary

Phase 8 is the operational capstone of the project. It delivers four distinct deliverables: (1) a `GET /health` FastAPI endpoint that returns system status using only DB/cached values within 100ms; (2) a daily arq cron at midnight UTC that creates a completed Todoist summary task and purges `sync_events` older than 90 days; (3) a standalone `scripts/e2e_test.py` that validates the full live webhook path before migration; and (4) a standalone `scripts/migrate.py` that links all existing Make.com task pairs into `sync_state` without creating duplicates.

All four components compose exclusively from modules already built in Phases 1–7. No new external libraries are needed. The health endpoint reads from `sync_events` (SQL count queries) and `kv_store` (KV reads) and queries the arq Redis queue depth via `ZCARD arq:queue`. The daily cron follows the exact same arq function signature pattern as `reconcile_sweep` and `orphan_sweep`. Both scripts use `asyncio.run()` with their own engine/session setup, mirroring `on_startup` in `app/worker/settings.py`.

The one non-obvious implementation detail is the `queue.failed` metric in the health response. Iterating `arq:result:*` keys for `success=False` is O(n) and will break the 100ms SLA at any meaningful queue size. The correct approach is to use `errors_24h` from `sync_events` (already required by OBS-1) as the failed-job proxy — this is already an O(1) SQL count query.

**Primary recommendation:** Implement in three plans: (1) health endpoint + health router, (2) daily summary cron + 90-day cleanup, (3) migration script + E2E test script.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01:** Migration script at `scripts/migrate.py`. Standalone, invoked `python scripts/migrate.py`. Run as Railway one-off or locally with env vars.

**D-02:** `--dry-run` flag required. Prints what it would do without writing. Must be previewed before touching live data.

**D-03:** If Zoho has a `Todoist_Task_ID` that no longer exists in Todoist (404): log warning, treat as if field were empty (create new Todoist task with footer, write new ID back to Zoho, store in `sync_state`). Do not abort migration.

**D-04:** E2E test at `scripts/e2e_test.py`. Standalone alongside `migrate.py`. Not part of pytest suite; invoked manually.

**D-05:** E2E test creates a real Zoho task via Zoho API at start, runs all assertions, deletes from both systems at end. Fully self-contained.

**D-06:** Propagation verification uses polling: check Todoist API every 5s for up to 90s. Pass if task appears within timeout; fail with clear error message if not.

**D-07:** After creating `Sync summary: {date}` Todoist task, immediately complete it. Acts as searchable log entry in completed tasks.

**D-08:** Content is counts only: `{N} syncs, {M} errors, {P} echoes suppressed`.

**D-09:** 90-day `sync_events` cleanup (OBS-4) runs in the same daily cron function — cleanup first, then create summary so counts reflect post-cleanup state.

**D-10:** Health status thresholds (Claude's discretion): `error` if queue has failed jobs > 0 OR `reconciler_last_run` is more than 30 min stale; `degraded` if `errors_24h` > 10; `ok` otherwise. All data from DB/kv_store only (no live API calls).

### Claude's Discretion

- Health status thresholds (ok/degraded/error) — defaults given in D-10
- Health endpoint router placement — new `app/health/router.py` or existing `app/webhooks/router.py`
- Whether `scripts/` uses `asyncio.run()` or a sync wrapper for async DB/API calls

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| OBS-1 | `GET /health` returns within 100ms using only DB/cached values; response shape and HTTP 200/503 rules | Health router pattern with SQL count queries + arq ZCARD; see Architecture Patterns §Health Endpoint |
| OBS-2 | All sync events logged to `sync_events` table with action/source/detail | Already implemented in Phases 5–7; health endpoint counts on this data |
| OBS-3 | Daily Todoist task: `Sync summary: {date}`, content `{N} syncs, {M} errors, {P} echoes suppressed` | arq cron with `hour=0, minute=0` — verified syntax in arq 0.28.0 |
| OBS-4 | `sync_events` cleanup: delete rows older than 90 days; run as part of daily cron | SQLAlchemy `delete()` with `created_at < cutoff`; runs in same cron as OBS-3 |
| SEED-1 | Migration mode, not fresh seed — link existing Make.com pairs, do NOT create duplicates | Migration algorithm documented in §Migration Script Pattern |
| SEED-2 | Migration algorithm: fetch all open Zoho tasks, link or create, store `sync_state` | Full algorithm with `--dry-run` and 404-fallback in §Migration Script Pattern |
| SEED-3 | Description migration: replace Make.com preamble entirely with `\n\n---\n[zoho:ID]` footer | `update_task(task_id, description=footer_only)` via existing `TodoistAPIAsync` |
| SEED-4 | Run E2E test before migration against live data; validates full webhook path | E2E polling pattern at 5s intervals / 90s timeout; see §E2E Test Pattern |
| INFRA-5 | All secrets as Railway env vars | Already satisfied by existing `Settings`; scripts load via `get_settings()` + `dotenv` |
</phase_requirements>

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Health endpoint | API / Backend (FastAPI `web` service) | DB + Redis | HTTP endpoint; reads DB counts and Redis ZCARD; no worker involvement |
| Daily summary cron | Worker (arq `worker` service) | Todoist API + DB | arq cron registered in WorkerSettings.cron_jobs; needs Todoist write + DB delete |
| 90-day cleanup | Worker (arq `worker` service) | DB | Runs in same cron function as summary; plain SQL DELETE |
| Migration script | Standalone script (local or Railway one-off) | Zoho API + Todoist API + DB | Not part of web or worker services; one-time operational script |
| E2E test script | Standalone script (local) | Zoho API + Todoist API + DB | Not part of any running service; manual pre-migration gate |

---

## Standard Stack

### Core (all already installed — no new dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fastapi | 0.136.0 | Health endpoint router | Already the web framework [VERIFIED: requirements.txt] |
| arq | 0.28.0 | Daily summary + cleanup cron | Already the job queue; `cron()` supports `hour=int, minute=int` [VERIFIED: arq 0.28.0 installed] |
| sqlalchemy[asyncio] | 2.0.49 | DB count queries + DELETE for cleanup | Already the ORM [VERIFIED: requirements.txt] |
| todoist-api-python | 4.0.0 | Daily summary task create/complete; migration Todoist ops | Already in use [VERIFIED: requirements.txt] |
| httpx | 0.28.1 | E2E test Zoho task creation (POST /crm/v8/Tasks) | Already used throughout codebase [VERIFIED: requirements.txt] |
| python-dotenv | >=1.0.0 | Scripts load .env for local runs | Already available [VERIFIED: requirements.txt] |

**Installation:** No new packages required. All dependencies already in `requirements.txt`.

---

## Architecture Patterns

### System Architecture Diagram

```
Daily Summary Cron (arq worker, midnight UTC)
  → DB: DELETE sync_events WHERE created_at < now()-90d
  → DB: COUNT(action='sync'), COUNT(action='error'), COUNT(action='echo_suppressed') in 24h window
  → Todoist API: add_task("Sync summary: {date}", content=counts, project_id=TODOIST_PROJECT_ID)
  → Todoist API: complete_task(new_task_id)

GET /health (FastAPI web, request.app.state.{redis,session_factory})
  → Redis: ZCARD arq:queue → depth
  → Redis: SCAN arq:in-progress:* → count
  → DB: SELECT last sync from sync_events (MAX created_at WHERE action='sync')
  → DB: COUNT(action='error', last 24h), COUNT(action='echo_suppressed', last 24h), COUNT(action='sync', last 24h)
  → DB: COUNT(*) from sync_state → active_tasks
  → DB: SELECT value FROM kv_store WHERE key='reconciler_last_run'
  → Compute status: ok/degraded/error per D-10 thresholds
  → Return JSON {status, last_sync, queue, errors_24h, echoes_suppressed_24h, syncs_24h, active_tasks, reconciler}

scripts/e2e_test.py (local/Railway one-off, asyncio.run)
  → Zoho API: POST /crm/v8/Tasks (create test task with ZOHO_USER_ID owner)
  → Poll Todoist every 5s up to 90s: look for task with [zoho:{new_id}] footer
  → Edit Zoho task title/due_date/priority via ZohoClient + verify propagation (poll)
  → Complete Zoho task → verify Todoist completion (poll)
  → DB: check sync_events for test task — assert no infinite loop
  → Cleanup: delete Todoist task + delete Zoho task

scripts/migrate.py (local/Railway one-off, asyncio.run, --dry-run flag)
  → Zoho API: fetch all open Tasks (owner=ZOHO_USER_ID, no modified_since filter — full scan)
  → For each task WHERE Todoist_Task_ID IS SET:
      → Todoist API: GET task/{id}
      → if 404: create new Todoist task (footer), write new ID to Zoho, upsert sync_state
      → if found: update_task(description=footer_only), upsert sync_state with canonical_hash
  → For each task WHERE Todoist_Task_ID IS EMPTY:
      → create_todoist_task(), write_todoist_id_to_zoho(), upsert sync_state
  → Print counts (dry-run or actual)
```

### Recommended Project Structure

```
app/
├── health/
│   ├── __init__.py
│   └── router.py         # GET /health endpoint
├── worker/
│   ├── settings.py       # add daily_summary to cron_jobs
│   └── daily_summary.py  # daily_summary() cron function
scripts/
├── e2e_test.py           # standalone E2E test (not pytest)
└── migrate.py            # standalone migration script
```

### Pattern 1: arq Cron Registration (midnight UTC)

**What:** Register a daily cron function in `WorkerSettings.cron_jobs` using integer `hour` and `minute` arguments.
**When to use:** Any recurring task that needs a specific daily schedule.

```python
# Source: arq 0.28.0 installed — verified cron(hour=0, minute=0) works
from arq import cron
from app.worker.daily_summary import daily_summary

class WorkerSettings:
    cron_jobs = [
        cron(reconcile_sweep, minute={0, 15, 30, 45}, second=0, timeout=300),
        cron(orphan_sweep,    minute={0},             second=0, timeout=600),
        cron(daily_summary,   hour=0, minute=0,       second=0, timeout=120),
    ]
```

**Key detail:** `hour=0, minute=0` (integers) works in arq 0.28.0. [VERIFIED: arq 0.28.0 installed, `cron(dummy, hour=0, minute=0, second=0)` produces valid CronJob]

### Pattern 2: Health Endpoint with 100ms SLA

**What:** FastAPI route that reads only pre-computed DB rows and Redis ZCARD — no live API calls.
**When to use:** System status checks where SLA is tight.

```python
# Source: FastAPI 0.136.0 (installed) + arq constants (verified)
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from arq.constants import default_queue_name, in_progress_key_prefix
from sqlalchemy import select, func, text

router = APIRouter()

@router.get("/health")
async def health(request: Request):
    redis = request.app.state.redis           # ArqRedis pool from lifespan
    session_factory = request.app.state.session_factory

    # Queue metrics from Redis (O(1))
    queue_depth = await redis.zcard(default_queue_name)
    # in_progress: count keys matching prefix (small count expected)
    in_progress = sum(1 async for _ in redis.scan_iter(in_progress_key_prefix + "*"))

    # DB metrics via count queries
    async with session_factory() as session:
        # ... count queries on sync_events + kv_store + sync_state

    status = _compute_status(...)
    http_code = 503 if status == "error" else 200
    return JSONResponse(content={...}, status_code=http_code)
```

**Critical:** `queue.failed` MUST NOT be computed by iterating `arq:result:*` keys. Use `errors_24h` from `sync_events` as the proxy (already required by OBS-1). This keeps the endpoint O(1) on DB and O(small) on Redis.

### Pattern 3: SQLAlchemy Count Queries for Health Data

**What:** Count `sync_events` rows by action within a 24-hour window.
**When to use:** All health endpoint metrics.

```python
# Source: SQLAlchemy 2.0.49 (installed), verified func.count available
from sqlalchemy import select, func
from datetime import datetime, timezone, timedelta
from app.db.models import SyncEvent, SyncState, KVStore

cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)

# Errors in last 24h
result = await session.execute(
    select(func.count()).where(
        SyncEvent.action == "error",
        SyncEvent.created_at > cutoff_24h,
    )
)
errors_24h = result.scalar_one()

# Active tasks
result = await session.execute(select(func.count()).select_from(SyncState))
active_tasks = result.scalar_one()

# reconciler_last_run from kv_store
result = await session.execute(
    select(KVStore.value).where(KVStore.key == "reconciler_last_run")
)
reconciler_last_run = result.scalar_one_or_none()
```

### Pattern 4: 90-Day Cleanup (SQLAlchemy DELETE)

**What:** Delete `sync_events` rows older than 90 days in the daily cron.
**When to use:** Audit log retention enforcement.

```python
# Source: SQLAlchemy 2.0.49 delete() — verified pattern
from sqlalchemy import delete
from datetime import datetime, timezone, timedelta
from app.db.models import SyncEvent

cutoff = datetime.now(timezone.utc) - timedelta(days=90)
result = await session.execute(
    delete(SyncEvent).where(SyncEvent.created_at < cutoff)
)
await session.commit()
deleted_count = result.rowcount
```

### Pattern 5: Daily Summary Cron Function

**What:** arq cron function that runs cleanup, then creates + immediately completes a Todoist summary task.
**When to use:** Daily scheduled job pattern.

```python
# Source: reconciler.py pattern (existing codebase) + D-07/D-08/D-09
from datetime import datetime, timezone
from app.db.models import SyncEvent
from app.todoist.client import TodoistClient

async def daily_summary(ctx: dict) -> None:
    session_factory = ctx["session_factory"]
    todoist_client: TodoistClient = ctx["todoist_client"]
    settings = get_settings()

    # D-09: cleanup FIRST, then count (post-cleanup state)
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    async with session_factory() as session:
        await session.execute(delete(SyncEvent).where(SyncEvent.created_at < cutoff))
        await session.commit()

    # Count for summary (24h window)
    async with session_factory() as session:
        # ... count syncs, errors, echoes

    today = datetime.now(timezone.utc).date().isoformat()
    content = f"{syncs} syncs, {errors} errors, {echoes} echoes suppressed"

    # Create task then immediately complete it (D-07)
    task = await todoist_client._api.add_task(
        content=f"Sync summary: {today}",
        description=content,
        project_id=settings.todoist_project_id,
    )
    await todoist_client._api.complete_task(task.id)
```

### Pattern 6: Standalone Script with asyncio.run()

**What:** Standalone Python script with its own DB engine + session setup, loaded via environment variables.
**When to use:** One-off migration and E2E scripts that need async DB + API access.

```python
# Source: mirrors on_startup in app/worker/settings.py
import asyncio
import argparse
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import get_settings
from app.zoho.client import ZohoClient
from app.todoist.client import TodoistClient
from app.zoho.state import token_state, zoho_field_cache
from app.zoho.token_manager import load_token_from_kv, refresh_access_token, upsert_kv

async def main(dry_run: bool) -> None:
    load_dotenv()
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Token bootstrap (mirrors on_startup)
    async with session_factory() as session:
        stored_token, stored_expires_at = await load_token_from_kv(session)
    # ... refresh if needed, populate token_state + zoho_field_cache ...

    zoho_client = ZohoClient(access_token=token_state["access_token"])
    todoist_client = TodoistClient(api_token=settings.todoist_api_token)

    try:
        await run_migration(zoho_client, todoist_client, session_factory, dry_run)
    finally:
        await todoist_client.close()
        await engine.dispose()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
```

**Important:** `get_settings()` is `lru_cache`'d — call `get_settings.cache_clear()` if needed after `load_dotenv()`.

### Pattern 7: Migration Script Algorithm

**What:** Link existing Make.com task pairs without creating duplicates.

```
for each zoho_task in fetch_all_open_zoho_tasks(owner=ZOHO_USER_ID):
    todoist_id = zoho_task.get(TODOIST_TASK_ID_FIELD)
    existing_state = await session.get(SyncState, zoho_task_id)

    if existing_state:
        log "already in sync_state, skipping" ; continue

    if todoist_id:
        try:
            todoist_task = await fetch_todoist_task(todoist_id)
            # SEED-3: replace entire description with footer only
            if not dry_run:
                await update_task(todoist_id, description=f"\n\n---\n[zoho:{zoho_task_id}]")
                norm = zoho_record_to_normalised(zoho_task, terminal_statuses)
                await upsert_sync_state(session, zoho_task_id, todoist_id, canonical_hash(norm))
            counts["linked"] += 1
        except TodoistNotFoundError:
            # D-03: 404 → create new, write back, store sync_state
            if not dry_run:
                new_id = await create_todoist_task(norm, zoho_task_id, api)
                await write_todoist_id_to_zoho(zoho_task_id, new_id, token)
                await upsert_sync_state(session, zoho_task_id, new_id, canonical_hash(norm))
            counts["recreated"] += 1
    else:
        # No Todoist ID at all: create fresh
        if not dry_run:
            new_id = await create_todoist_task(norm, zoho_task_id, api)
            await write_todoist_id_to_zoho(zoho_task_id, new_id, token)
            await upsert_sync_state(session, zoho_task_id, new_id, canonical_hash(norm))
        counts["created"] += 1
```

**Critical:** Check for existing `sync_state` row FIRST to ensure idempotency. If migrated twice, second run is a no-op.

### Pattern 8: Zoho Task Creation (E2E test only)

**What:** POST to Zoho v8 Tasks to create a test task — not in existing codebase.
**When to use:** E2E test setup; migration script does NOT create Zoho tasks.

```python
# Source: Zoho CRM v8 API + existing httpx pattern in app/zoho/writer.py
import httpx
from app.zoho.client import ZOHO_EU_BASE_URL

async def create_zoho_task(subject: str, owner_id: str, access_token: str) -> str:
    """Returns new Zoho task ID."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ZOHO_EU_BASE_URL}/Tasks",
            headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
            json={"data": [{"Subject": subject, "Owner": {"id": owner_id}}]},
        )
    resp.raise_for_status()
    data = resp.json()
    return str(data["data"][0]["details"]["id"])
```

### Pattern 9: E2E Polling Loop

**What:** Poll Todoist every 5s for up to 90s waiting for task to appear.
**When to use:** Validating the full webhook → Redis → worker → Todoist write path.

```python
# Source: D-06, todoist-api-python SDK
import asyncio
from app.todoist.client import TodoistClient, TodoistNotFoundError

async def wait_for_todoist_task(
    todoist_client: TodoistClient,
    zoho_task_id: str,
    timeout_s: int = 90,
    interval_s: int = 5,
) -> str:
    """Poll Todoist project tasks for [zoho:{zoho_task_id}] footer. Returns todoist_task_id."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        tasks = await todoist_client._api.get_tasks(project_id=settings.todoist_project_id)
        for t in tasks:
            if f"[zoho:{zoho_task_id}]" in (t.description or ""):
                return t.id
        await asyncio.sleep(interval_s)
    raise AssertionError(
        f"Task [zoho:{zoho_task_id}] not found in Todoist after {timeout_s}s"
    )
```

### Anti-Patterns to Avoid

- **Iterating arq:result:* for `queue.failed`:** O(n) over all completed jobs; breaks 100ms SLA. Use `errors_24h` from `sync_events` as proxy instead.
- **Using `KEYS arq:in-progress:*`:** Blocks Redis server. Use `scan_iter()` instead.
- **Calling `get_settings()` before `load_dotenv()`:** Settings are `lru_cache`'d at module import; the cached values won't see `.env`. Call `get_settings.cache_clear()` after `load_dotenv()` in scripts.
- **Passing `description` in `update_todoist_task()`:** The existing `update_todoist_task()` in `app/todoist/writer.py` explicitly never passes description (protects the footer). The migration script must call `todoist_client._api.update_task(task_id, description=footer_only)` directly, NOT through `update_todoist_task()`.
- **Appending to Make.com description:** SEED-3 is explicit — replace entirely. The Make.com description content is discarded; only the `\n\n---\n[zoho:ID]` footer remains.
- **Daily summary task staying open:** Create then immediately complete (D-07). An open summary task in the active list would conflict with the synced Zoho tasks and could trigger sync loops.
- **Counting post-cleanup before cleanup runs:** D-09 says cleanup runs first, THEN count. Reversed order would count events that are then deleted, causing misleading zero counts.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Counting sync events | Custom aggregation | SQLAlchemy `func.count()` + `where()` | Already in project; single query per metric |
| Queue depth | Redis `LLEN` guessing | `redis.zcard("arq:queue")` | arq stores pending jobs in a sorted set, not a list; LLEN would return 0 |
| Token bootstrap in scripts | New auth flow | Reuse `load_token_from_kv` + `refresh_access_token` from `app.zoho.token_manager` | Already handles persistence, expiry, EU region |
| Field cache in scripts | Re-implement metadata fetch | Call `zoho_client.get_fields_metadata("Tasks")` and populate `zoho_field_cache` | `write_todoist_id_to_zoho` reads from `zoho_field_cache` directly |
| Canonical hash computation | Re-implement | `canonical_hash(zoho_record_to_normalised(...))` from Phase 1 | Hash correctness is critical; must use same function as sync_task |

---

## Common Pitfalls

### Pitfall 1: `queue.failed` via `all_job_results()` Breaks SLA
**What goes wrong:** Health endpoint takes > 100ms because `all_job_results()` iterates every `arq:result:*` Redis key.
**Why it happens:** The obvious way to get failed jobs is `pool.all_job_results()`, but this is O(n) over all recent job results.
**How to avoid:** For the health endpoint, use `errors_24h` from `sync_events` as the failed-job proxy. OBS-1 requires `errors_24h` anyway — no extra work.
**Warning signs:** Health endpoint test shows latency > 50ms in a loaded environment.

### Pitfall 2: lru_cache Prevents `.env` from Loading in Scripts
**What goes wrong:** Script loads `.env` via `load_dotenv()` but `get_settings()` returns values from the cached (pre-`load_dotenv`) call.
**Why it happens:** `get_settings()` is decorated with `@lru_cache(maxsize=1)`. On first import of `app.core.config`, Settings() is called before `.env` is loaded.
**How to avoid:** Call `get_settings.cache_clear()` immediately after `load_dotenv()`. Or import `get_settings` lazily inside `async def main()` after the dotenv call.
**Warning signs:** Script fails with missing env vars even though `.env` exists.

### Pitfall 3: Migration Runs on Already-Migrated Tasks
**What goes wrong:** Running migrate.py twice creates duplicate `sync_state` rows or overwrites `last_hash` with stale values.
**Why it happens:** No idempotency guard.
**How to avoid:** Check `SELECT sync_state WHERE zoho_task_id = ?` FIRST. If a row already exists, skip that task entirely. Log as "already linked".
**Warning signs:** `sync_state` row count exceeds number of open Zoho tasks.

### Pitfall 4: E2E Test Leaves Artifacts on Failure
**What goes wrong:** E2E test fails mid-run and leaves a Zoho test task and/or Todoist task in the live account permanently.
**Why it happens:** Cleanup only runs at the end of a successful path.
**How to avoid:** Use try/finally in the script to always delete created tasks, even if assertions fail. Track created IDs in variables from the moment of creation.
**Warning signs:** Stale tasks named like the test prefix appearing in Zoho/Todoist.

### Pitfall 5: `update_todoist_task()` Strips the Footer
**What goes wrong:** Migration uses `app.todoist.writer.update_todoist_task()` to update description — but that function explicitly NEVER passes description to protect the footer.
**Why it happens:** The writer was designed to protect the footer from sync operations. The migration has the opposite need: SET the description to the footer.
**How to avoid:** Migration script calls `todoist_client._api.update_task(task_id, description=f"\n\n---\n[zoho:{zoho_id}]")` directly.
**Warning signs:** Todoist tasks after migration have no footer, causing sync_task to treat them as native tasks.

### Pitfall 6: Health Endpoint Router Not Registered in app/main.py
**What goes wrong:** `GET /health` returns 404.
**Why it happens:** New router created but not `include_router`'d in the FastAPI app.
**How to avoid:** Add `app.include_router(health_router, tags=["health"])` in `app/main.py` alongside the webhooks router.
**Warning signs:** `/health` returns 404 in testing.

### Pitfall 7: Daily Summary Creates a Task in Wrong Project
**What goes wrong:** Summary task appears in Todoist inbox instead of the synced project.
**Why it happens:** `add_task()` called without `project_id`.
**How to avoid:** Always pass `project_id=settings.todoist_project_id` (D-07 says same project as synced tasks).
**Warning signs:** Summary task not found in `6gCPcWwM392GhXQh` project.

---

## Code Examples

### Health Status Logic (D-10 thresholds)

```python
# Source: D-10 from CONTEXT.md + verified via research
from datetime import datetime, timezone, timedelta

def compute_health_status(
    errors_24h: int,
    reconciler_last_run: str | None,
    queue_failed: int,  # use errors_24h as proxy, or track separately
) -> str:
    now = datetime.now(timezone.utc)

    # Error conditions (HTTP 503)
    if queue_failed > 0:
        return "error"
    if reconciler_last_run is None:
        return "error"
    try:
        last_run = datetime.fromisoformat(reconciler_last_run)
        if (now - last_run) > timedelta(minutes=30):
            return "error"
    except ValueError:
        return "error"

    # Degraded (HTTP 200)
    if errors_24h > 10:
        return "degraded"

    return "ok"
```

### last_sync Query (most recent sync event + direction)

```python
# Source: SQLAlchemy 2.0.49 + SyncEvent model (verified)
from sqlalchemy import select
from app.db.models import SyncEvent

result = await session.execute(
    select(SyncEvent)
    .where(SyncEvent.action == "sync")
    .order_by(SyncEvent.created_at.desc())
    .limit(1)
)
last_event = result.scalar_one_or_none()
last_sync = None
if last_event:
    last_sync = {
        "at": last_event.created_at.isoformat(),
        "source": last_event.source,  # "zoho_webhook", "todoist_webhook", or "reconciler"
    }
```

### Fetching All Open Zoho Tasks (Migration, full scan)

```python
# Source: zoho/client.py fetch pattern + Zoho v8 API
# Migration needs ALL open tasks assigned to user — no modified_since filter
async def fetch_all_open_zoho_tasks(zoho_client: ZohoClient, owner_id: str) -> list[dict]:
    """Fetch all open Tasks assigned to owner_id, paginated."""
    criteria = f"((Status:not_equal:Completed)and(Owner:equals:{owner_id}))"
    results = []
    page = 1
    while True:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{ZOHO_EU_BASE_URL}/Tasks/search",
                params={"criteria": criteria, "page": page, "per_page": 200},
                headers={"Authorization": f"Zoho-oauthtoken {zoho_client.access_token}"},
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
```

**Note:** The `criteria` filter for migration differs from `fetch_tasks_modified_since` — it uses `Status:not_equal:Completed` instead of `Modified_Time`. [ASSUMED — Zoho criteria syntax follows existing pattern in client.py; terminal statuses beyond "Completed" may not be covered by this filter, which is acceptable for migration since Make.com only ran against open tasks]

---

## Runtime State Inventory

> Included because migration modifies live system state.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `sync_state` table — 0 rows before migration (migration populates it); `sync_events` table — existing rows from previous phases (Phase 7 already writes to this) | Migration script populates `sync_state`; no migration of existing `sync_events` |
| Live service config | Zoho `Todoist_Task_ID` custom field — already populated by Make.com for all open tasks; migration reads then may write-back new IDs for 404-fallback tasks | No schema change; only value updates for 404-fallback cases |
| OS-registered state | None — Railway service registration is infra, not code | None |
| Secrets/env vars | All existing env vars in `Settings` — no new vars for Phase 8; INFRA-5 satisfied | None — no new env vars required |
| Build artifacts | None — no compiled artifacts affected | None |

**Todoist descriptions (live data):** All existing tasks in project `6gCPcWwM392GhXQh` have Make.com descriptions in format `Re: {related_to}\n[Zoho Task link]...`. Migration REPLACES these entirely with `\n\n---\n[zoho:{ID}]`. This is intentional and irreversible per SEED-3.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11+ | Scripts (`asyncio.run`, `datetime.fromisoformat` with TZ) | ✓ | 3.11 | — |
| arq 0.28.0 | Daily cron | ✓ | 0.28.0 | — |
| todoist-api-python 4.0.0 | Daily summary, migration, E2E | ✓ | 4.0.0 | — |
| fastapi 0.136.0 | Health endpoint | ✓ | 0.136.0 | — |
| httpx 0.28.1 | E2E Zoho task create | ✓ | 0.28.1 | — |
| SQLAlchemy 2.0.49 | Health count queries, cleanup DELETE | ✓ | 2.0.49 | — |
| Postgres (live) | Migration, health | Required at runtime | Railway-managed | — |
| Redis (live) | Health queue depth | Required at runtime | Railway-managed | — |
| Zoho CRM API (live) | E2E test, migration | Required at runtime | EU region | — |
| Todoist API (live) | E2E test, migration, daily cron | Required at runtime | — | — |

**Missing dependencies with no fallback:** None — all code dependencies already installed. Live API/DB access required for migration and E2E (by design).

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio |
| Config file | `pyproject.toml` (`asyncio_mode = "auto"`) |
| Quick run command | `pytest tests/unit/test_health.py tests/unit/test_daily_summary.py tests/unit/test_migration.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| OBS-1 | `/health` returns correct JSON shape and HTTP 200/503 per status | unit | `pytest tests/unit/test_health.py -x` | ❌ Wave 0 |
| OBS-1 | Health status is `error` when reconciler_last_run > 30min stale | unit | `pytest tests/unit/test_health.py::test_health_error_stale_reconciler -x` | ❌ Wave 0 |
| OBS-1 | Health status is `degraded` when errors_24h > 10 | unit | `pytest tests/unit/test_health.py::test_health_degraded -x` | ❌ Wave 0 |
| OBS-1 | Health returns 503 for `error` status, 200 for `ok`/`degraded` | unit | `pytest tests/unit/test_health.py::test_health_http_status -x` | ❌ Wave 0 |
| OBS-3 | `daily_summary` creates Todoist task with correct title format | unit | `pytest tests/unit/test_daily_summary.py::test_daily_summary_task_title -x` | ❌ Wave 0 |
| OBS-3/D-07 | `daily_summary` completes the task after creating it | unit | `pytest tests/unit/test_daily_summary.py::test_daily_summary_task_completed -x` | ❌ Wave 0 |
| OBS-4/D-09 | `daily_summary` runs cleanup before counting (cleanup count is accurate) | unit | `pytest tests/unit/test_daily_summary.py::test_cleanup_before_count -x` | ❌ Wave 0 |
| OBS-4 | Cleanup deletes rows older than 90 days, leaves newer rows | unit | `pytest tests/unit/test_daily_summary.py::test_90day_cleanup -x` | ❌ Wave 0 |
| SEED-1/SEED-2 | Migration skips tasks already in `sync_state` (idempotency) | unit | `pytest tests/unit/test_migration.py::test_already_linked_skipped -x` | ❌ Wave 0 |
| SEED-2 | Migration links task with existing Todoist ID and updates description | unit | `pytest tests/unit/test_migration.py::test_link_existing_pair -x` | ❌ Wave 0 |
| SEED-2/D-03 | Migration handles 404 Todoist ID (create new + write-back) | unit | `pytest tests/unit/test_migration.py::test_todoist_404_fallback -x` | ❌ Wave 0 |
| SEED-2 | Migration creates Todoist task for Zoho tasks with no ID | unit | `pytest tests/unit/test_migration.py::test_create_for_empty_id -x` | ❌ Wave 0 |
| SEED-3 | Migration replaces (not appends) Make.com description | unit | `pytest tests/unit/test_migration.py::test_description_replaced_not_appended -x` | ❌ Wave 0 |
| D-02 | `--dry-run` prints counts without writing to DB or Todoist | unit | `pytest tests/unit/test_migration.py::test_dry_run_no_writes -x` | ❌ Wave 0 |
| SEED-4 | E2E test creates/edits/completes/cleans up test task pair | manual | Run `python scripts/e2e_test.py` before migration | N/A (not pytest) |
| INFRA-5 | All required env vars documented and validated by Settings | unit | `pytest tests/unit/test_config.py -x` (existing) | ✅ |

### Sampling Rate
- **Per task commit:** `pytest tests/unit/test_health.py tests/unit/test_daily_summary.py tests/unit/test_migration.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/test_health.py` — covers OBS-1 health endpoint shape, status logic, HTTP codes
- [ ] `tests/unit/test_daily_summary.py` — covers OBS-3, OBS-4, D-07, D-08, D-09
- [ ] `tests/unit/test_migration.py` — covers SEED-1, SEED-2, SEED-3, D-02, D-03
- [ ] `scripts/` directory — create for migration and E2E scripts

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Health endpoint is internal-only; no auth added (consistent with existing webhook handlers) |
| V3 Session Management | no | Stateless HTTP endpoint |
| V4 Access Control | no | Internal service endpoint, not user-facing |
| V5 Input Validation | no | Health endpoint takes no user input; migration/E2E use env-var-sourced IDs only |
| V6 Cryptography | no | No new cryptographic operations |

**Note:** The health endpoint (GET /health) is unauthenticated. This is consistent with the project's design (internal Railway service, no public user authentication). [ASSUMED — no auth requirement stated in REQUIREMENTS.md or CONTEXT.md]

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Migration script run against wrong environment | Tampering | `--dry-run` flag (D-02); requires explicit env vars pointing to production |
| E2E test artifacts in production if script crashes | Tampering | try/finally cleanup in `e2e_test.py` (Pitfall 4) |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `Status:not_equal:Completed` is valid Zoho criteria syntax for migration's full task scan | Code Examples §fetch_all_open_zoho_tasks | Migration may fail to fetch tasks or fetch wrong set; mitigation: use `fetch_tasks_modified_since` with a very old timestamp as alternative |
| A2 | Health endpoint is unauthenticated (internal-only access acceptable) | Security Domain | If Railway exposes `/health` publicly, unauthenticated access to system metrics; mitigation: add Railway-level network policy |
| A3 | `ZOHO_TERMINAL_STATUSES` covers all terminal statuses in this org for migration (tasks with "Completed" status are already closed and won't appear in "not_equal:Completed" filter) | Migration Script Pattern | If org has custom terminal statuses, some completed tasks might be re-fetched; low risk since migration only links open tasks |

---

## Open Questions

1. **Zoho criteria syntax for `Status:not_equal:Completed`**
   - What we know: `criteria` syntax `field:operator:value` is used in `fetch_tasks_modified_since` with `greater_equal` and `equals`
   - What's unclear: Whether `not_equal` is a valid operator (vs `not_equal` or `!=`)
   - Recommendation: Test `not_equal` in migration script; fall back to fetching ALL tasks and filtering client-side if needed (simpler, acceptable for one-time migration)

2. **How to get `last_sync.direction` from `sync_events`**
   - What we know: `source` column is `zoho_webhook|todoist_webhook|reconciler|migration`; the direction (zoho→todoist vs todoist→zoho) is not explicitly stored
   - What's unclear: OBS-1 says `last_sync` includes "direction" — whether this means source or a zoho/todoist directional field
   - Recommendation: Use `source` as the direction proxy (`zoho_webhook` → direction=`zoho_to_todoist`; `todoist_webhook` → `todoist_to_zoho`; `reconciler` → `reconciler`)

---

## Sources

### Primary (HIGH confidence)
- arq 0.28.0 installed — `cron()` signature verified, `hour=int, minute=int` syntax confirmed [VERIFIED: pip show arq, python3 import]
- todoist-api-python 4.0.0 installed — `add_task`, `complete_task`, `update_task`, `get_task`, `delete_task` signatures verified [VERIFIED: inspect.signature]
- FastAPI 0.136.0 — `JSONResponse` with `status_code=503` pattern available [VERIFIED: pip show fastapi]
- SQLAlchemy 2.0.49 — `func.count()`, `delete()`, `select()` all verified [VERIFIED: python3 import]
- arq constants — `default_queue_name="arq:queue"`, `in_progress_key_prefix="arq:in-progress:"` [VERIFIED: arq.constants source]
- ArqRedis — `zcard` and `scan_iter` available [VERIFIED: inspect dir(ArqRedis)]

### Secondary (MEDIUM confidence)
- Existing codebase patterns — `app/worker/settings.py`, `app/worker/reconciler.py`, `app/webhooks/router.py`, `app/zoho/writer.py`, `app/todoist/writer.py` — all read directly [VERIFIED: Read tool]

### Tertiary (LOW confidence)
- Zoho `Status:not_equal:Completed` criteria syntax — inferred from existing `not_equal` operator pattern, not verified against live API

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages installed and verified
- Architecture: HIGH — all patterns derived from existing codebase
- Pitfalls: HIGH — discovered through code inspection and arq internals
- Migration algorithm: HIGH — derived from CONTEXT.md decisions and verified against existing writer functions
- Zoho criteria for migration: LOW — not tested against live API

**Research date:** 2026-04-25
**Valid until:** 2026-05-25 (stable stack, no fast-moving dependencies)
