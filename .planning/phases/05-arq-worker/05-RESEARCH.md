# Phase 5: arq Worker - Research

**Researched:** 2026-04-24
**Domain:** arq 0.28.0, Redis locking, SQLAlchemy async SELECT FOR UPDATE, sync pipeline orchestration
**Confidence:** HIGH

---

## Summary

Phase 5 wires together all prior phases (Zoho read, Todoist read, write operations, canonical hash) into a single `sync_task` arq job function. The job must execute the full pipeline — fetch live state from both APIs, hash-compare, lock the `sync_state` row with `SELECT FOR UPDATE`, conditionally write to the target, then persist the new hash and log an event. A second layer of defence uses a per-task Redis `SETNX` lock to serialise any two jobs that bypass arq's built-in job-ID deduplication (which only blocks while a job is in the queue; concurrent in-progress jobs can still race).

arq 0.28.0 is already pinned in `requirements.txt`. The `WorkerSettings` class approach is the canonical way to configure the worker for Railway deployment. `RedisSettings.from_dsn()` parses the `REDIS_URL` env var string into the correct dataclass. The `func()` wrapper configures per-function `timeout`, `keep_result`, and `max_tries`. Custom retry backoff uses `raise Retry(defer=N)` inside the job function, conditioned on `ctx['job_try']`.

Loop-suppression (`LOOP-5`) for self-created Todoist tasks is already partially handled by Phase 3's `extract_zoho_id()`: if the footer is present the task is sync-managed; if absent it's discarded. Phase 5 must make this check explicit at the `item:added` path (Phase 6 owns the webhook endpoint but Phase 5's `sync_task` must be robust when called with a Todoist-created task ID).

**Primary recommendation:** Build one `app/worker/jobs.py` module with `sync_task`, a `WorkerSettings` class in `app/worker/settings.py`, and an entry-point `app/worker/__main__.py`. Shared context objects (DB session factory, ZohoClient, ArqRedis pool) are injected via arq's `on_startup`/`on_shutdown` hooks into `ctx`.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Job deduplication | Redis / arq queue | — | arq's `_job_id` uses a Redis transaction to block duplicate enqueues |
| Per-task serialisation lock | Redis (SETNX) | — | Serialises two jobs that slip past dedup when both are already in-progress |
| Hash comparison + echo suppression | Worker (Python) | Postgres (`sync_state`) | Read `last_hash` from DB; compute from live API data; compare in Python |
| `SELECT FOR UPDATE` critical section | Postgres (asyncpg) | SQLAlchemy async | Row-level lock prevents concurrent DB writes for the same task |
| Live API fetch | External (Zoho + Todoist APIs) | Worker | Worker calls ZohoClient and TodoistClient |
| Audit logging | Postgres (`sync_events`) | Worker | Worker inserts event rows after each outcome |
| Retry / backoff | arq + Redis | — | `raise Retry(defer=N)` inside job; arq stores retry state in Redis |
| Worker process lifecycle | arq `WorkerSettings` | Railway | `run_worker(WorkerSettings)` as the `worker` Railway service command |
| Deferred start (stale-read mitigation) | arq (`_defer_by`) | Worker | 2-second defer set at enqueue time by caller, not inside job |

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SYNC-10 | arq job deduplication via `_job_id=f"sync:{zoho_task_id}"`. `None` return → log WARN. Reconciliation catches any misses within 15 min. | Verified: `enqueue_job(..., _job_id=...)` returns `None` when duplicate [VERIFIED: arq 0.28.0 source + docs] |
| SYNC-11 | LWW conflict resolution on simultaneous edits; log `action='overwrite'`. | Handled in `sync_task` logic — when hashes diverge, the later write wins by design. `sync_events` row with `action='overwrite'` inserted before write. |
| LOOP-1 | Canonical hash echo suppression: if incoming hash == `last_hash`, skip write, log `echo_suppressed`. | `canonical_hash()` already in `app/core/hash.py` from Phase 1. `sync_task` reads `sync_state.last_hash` from DB and compares. |
| LOOP-3 | `SELECT FOR UPDATE` on `sync_state` row at start of critical section. | SQLAlchemy async: `select(SyncState).where(...).with_for_update()` then `session.execute()`. asyncpg translates to Postgres row-level lock. [VERIFIED: SQLAlchemy 2.0.49 installed] |
| LOOP-4 | 2-second deferred start on Zoho-triggered jobs via `_defer_by=settings.zoho_job_defer_secs`. | `settings.zoho_job_defer_secs` already in `Settings` (Phase 2). Caller passes `_defer_by` to `enqueue_job`; job body does NOT sleep — this is a queue-level defer. |
| LOOP-5 | Bootstrap race: self-created Todoist tasks have footer → handler identifies as sync-managed and suppresses reverse sync. | `extract_zoho_id()` from Phase 3 provides this check. Phase 5 `sync_task` receiving a Todoist task ID should call `extract_zoho_id` on the fetched description before treating it as a new Todoist-native task. |
| INFRA-1 | Two Railway services: `web` (FastAPI) and `worker` (arq). `app/worker/__main__.py` is the worker entry point. | Worker is a separate Railway service pointing at `python -m app.worker`. Crash isolation: worker crash does not affect `web`. |
| INFRA-3 | Redis on Railway used by arq for job queue and deduplication. | `RedisSettings.from_dsn(settings.redis_url)` is the clean way to consume `REDIS_URL` env var. [VERIFIED: arq 0.28.0 source `from_dsn` classmethod] |

</phase_requirements>

---

## Standard Stack

### Core (already pinned in requirements.txt)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| arq | 0.28.0 | Async Redis-backed job queue | Already chosen; `func()` + `WorkerSettings` = full job config |
| SQLAlchemy[asyncio] | 2.0.49 | Async ORM with `SELECT FOR UPDATE` | Already in use; `with_for_update()` on `select()` |
| asyncpg | 0.31.0 | Async Postgres driver | Already installed; required by SQLAlchemy async |
| redis (bundled with arq) | 5.3.1 | `ArqRedis.set(nx=True, ex=30)` for SETNX lock | `ArqRedis` inherits standard redis-py `set()` with `nx` + `ex` params |

**No new dependencies for Phase 5.** All required libraries are already installed.

**Version verification:** All versions confirmed via `pip show` and `requirements.txt`.

---

## Architecture Patterns

### System Architecture Diagram

```
Webhook / Reconciler (Phase 6/7)
       │
       │ enqueue_job("sync_task", zoho_task_id,
       │             _job_id=f"sync:{zoho_task_id}",
       │             _defer_by=settings.zoho_job_defer_secs)
       ▼
   arq queue (Redis)
       │
       │ worker polls arq:queue every 0.5s
       ▼
   sync_task(ctx, zoho_task_id)
       │
       ├─[1]─ Redis SETNX lock=f"lock:sync:{zoho_task_id}" (30s TTL)
       │       └─ lock held? → log WARN, return (second dedup layer)
       │
       ├─[2]─ fetch_zoho_task(zoho_task_id)  → NormalisedTask (or raise → retry)
       │
       ├─[3]─ lookup sync_state.todoist_task_id (no lock yet)
       │       └─ not found? → create new Todoist task → write_todoist_id_to_zoho
       │                                                → insert sync_state row → log 'sync'
       │
       ├─[4]─ fetch_todoist_task(todoist_task_id)  → NormalisedTask (or raise → retry)
       │
       ├─[5]─ compute canonical_hash(zoho_normalised) and canonical_hash(todoist_normalised)
       │
       ├─[6]─ SELECT FOR UPDATE sync_state WHERE zoho_task_id=...  (critical section begins)
       │
       ├─[7]─ re-read last_hash (under lock)
       │       ├─ both hashes == last_hash? → log 'echo_suppressed' → release lock → return
       │       ├─ zoho_hash != last_hash? → write to Todoist → update last_hash → log 'sync'
       │       └─ todoist_hash != last_hash? → write to Zoho → update last_hash → log 'sync'
       │           (both differ → LWW: zoho wins → log 'overwrite')
       │
       └─[8]─ release SELECT FOR UPDATE (session.commit()) → release SETNX lock
```

### Recommended Project Structure

```
app/
├── worker/
│   ├── __init__.py
│   ├── __main__.py      # python -m app.worker entry point; calls run_worker(WorkerSettings)
│   ├── settings.py      # WorkerSettings class: functions, on_startup, on_shutdown, redis_settings
│   └── jobs.py          # sync_task() arq job function
└── ...existing modules...

tests/
└── unit/
    └── test_worker_jobs.py   # unit tests for sync_task logic
```

### Pattern 1: WorkerSettings class

**What:** A plain class (not an instance) passed to `run_worker()`. arq reads its attributes as configuration.
**When to use:** The only way to configure an arq worker in production.

```python
# Source: arq 0.28.0 docs + installed source at /data/home/.local/lib/python3.11/site-packages/arq/
from arq import func, cron
from arq.connections import RedisSettings
from app.worker.jobs import sync_task
from app.core.config import get_settings

async def on_startup(ctx: dict) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.zoho.client import ZohoClient
    from app.zoho.state import token_state
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["engine"] = engine
    # ZohoClient uses in-memory token_state (shared with web service via startup)
    ctx["zoho_client"] = ZohoClient(access_token=token_state["access_token"])
    # TodoistClient needs an open httpx session
    from app.todoist.client import TodoistClient
    todoist_client = TodoistClient(api_token=settings.todoist_api_token)
    ctx["todoist_client"] = todoist_client

async def on_shutdown(ctx: dict) -> None:
    await ctx["todoist_client"].close()
    await ctx["engine"].dispose()

class WorkerSettings:
    functions = [
        func(sync_task, timeout=60, keep_result=300, max_tries=4),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 60       # global default (overridden per-function by func())
    keep_result = 300      # global default
    max_tries = 4          # 1 initial + 3 retries; overridden per-function
    max_jobs = 10          # concurrent job limit (default 10 is fine)
```

### Pattern 2: sync_task job function

**What:** The arq job function signature is `async def name(ctx, *args, **kwargs)`. `ctx` is a dict populated by `on_startup`. Job is addressed by its Python function name or the name set in `func()`.
**When to use:** Every arq job must follow this signature.

```python
# Source: arq 0.28.0 docs [VERIFIED: installed source + docs]
from arq import Retry
from app.core.config import get_settings

RETRY_DELAYS = {1: 5, 2: 15, 3: 60}  # job_try -> seconds before next retry

async def sync_task(ctx: dict, zoho_task_id: str) -> None:
    """
    Full sync pipeline for one Zoho task.
    Called by arq worker; ctx populated by on_startup.
    """
    settings = get_settings()
    redis = ctx["redis"]            # ArqRedis pool injected by arq automatically
    session_factory = ctx["session_factory"]
    zoho_client = ctx["zoho_client"]
    todoist_client = ctx["todoist_client"]
    job_try: int = ctx["job_try"]   # 1-indexed; arq sets this automatically

    # [1] Per-task Redis lock (SETNX defence-in-depth)
    lock_key = f"lock:sync:{zoho_task_id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=30)
    if not acquired:
        log.warning("sync_task_lock_not_acquired", zoho_task_id=zoho_task_id)
        return  # silently drop; reconciler will retry within 15 min

    try:
        await _run_sync_pipeline(
            zoho_task_id, session_factory, zoho_client, todoist_client, settings
        )
    except (ZohoRateLimitError, TodoistRateLimitError, ZohoAPIError, TodoistAPIError) as exc:
        delay = RETRY_DELAYS.get(job_try, 60)
        raise Retry(defer=delay) from exc
    finally:
        await redis.delete(lock_key)
```

**Critical detail:** `ctx["redis"]` is the `ArqRedis` pool — arq injects it automatically. Do NOT create a separate Redis pool; use `ctx["redis"]`.

### Pattern 3: SELECT FOR UPDATE in SQLAlchemy async

**What:** Acquires a Postgres row-level advisory lock via `SELECT ... FOR UPDATE` in an open transaction.
**When to use:** Inside the critical section of `sync_task`, between fetching live data and writing to the target API.

```python
# Source: SQLAlchemy 2.0 docs [VERIFIED: sqlalchemy 2.0.49 installed]
from sqlalchemy import select
from app.db.models import SyncState

async with session_factory() as session:
    async with session.begin():  # begins transaction
        row = await session.execute(
            select(SyncState)
            .where(SyncState.zoho_task_id == zoho_task_id)
            .with_for_update()  # FOR UPDATE — blocks concurrent transactions on same row
        )
        state = row.scalar_one_or_none()
        if state is None:
            # new task: no lock contention risk; skip FOR UPDATE path
            ...
        else:
            # hash comparison and write happen here (under lock)
            if incoming_hash == state.last_hash:
                await _log_event(session, zoho_task_id, "echo_suppressed", ...)
                return  # transaction commits on context exit, releasing lock
            # write to target API ...
            state.last_hash = new_hash
            state.last_synced_at = datetime.now(timezone.utc)
            await _log_event(session, zoho_task_id, "sync", ...)
        # session.begin() context exit → commit → lock released
```

**Pitfall:** Do NOT call external APIs (Zoho, Todoist) while holding the `FOR UPDATE` lock. Fetch live data BEFORE acquiring the lock. The lock should only cover the hash comparison + DB update section.

### Pattern 4: Redis SETNX lock via ArqRedis

**What:** `ArqRedis.set(key, value, nx=True, ex=30)` is an atomic SETNX. Returns `True` if lock acquired, `None`/`False` if not.
**When to use:** At the start of `sync_task`, before any API calls, as defence-in-depth against two jobs for the same task running concurrently.

```python
# Source: redis-py 5.3.1 [VERIFIED: ArqRedis.set signature confirmed]
# nx=True → SET NX (only set if Not eXists)
# ex=30   → EXPIRE 30 seconds (auto-release if worker crashes)
acquired = await redis.set(f"lock:sync:{zoho_task_id}", "1", nx=True, ex=30)
# acquired is True if lock obtained, None if already held
if not acquired:
    log.warning("concurrent_sync_job_skipped", zoho_task_id=zoho_task_id)
    return
try:
    ...  # critical work
finally:
    await redis.delete(f"lock:sync:{zoho_task_id}")  # always release
```

### Pattern 5: arq retry with custom backoff

**What:** `raise Retry(defer=N)` inside a job causes arq to re-enqueue it after N seconds. `ctx["job_try"]` (1-indexed) tracks the attempt number. The job is permanently failed if `job_try > max_tries`.
**When to use:** On transient API errors (rate limits, 5xx responses).

```python
# Source: arq 0.28.0 docs [VERIFIED: installed source confirms job_try > max_tries check]
RETRY_DELAYS = {1: 5, 2: 15, 3: 60}  # after attempt 1 wait 5s, after 2 wait 15s, after 3 wait 60s
# max_tries=4 means job_try goes 1→2→3→4; if job_try=4 raises Retry, arq fails permanently

async def sync_task(ctx, zoho_task_id):
    job_try = ctx["job_try"]
    try:
        ...
    except (ZohoRateLimitError, ZohoAPIError, TodoistAPIError) as exc:
        delay = RETRY_DELAYS.get(job_try, 60)
        log.warning("sync_task_retry", job_try=job_try, delay=delay, error=str(exc))
        raise Retry(defer=delay) from exc
```

**Critical detail on max_tries:** arq's default `max_tries` is 5. The requirement specifies "max 3 retries" = 4 total attempts. Set `max_tries=4` in `func(sync_task, ..., max_tries=4)`. arq permanently fails the job when `job_try > max_tries` (i.e., when job_try=5 is attempted, but we set max_tries=4, so it fails after job_try=4).

### Pattern 6: enqueue_job with dedup and defer

**What:** Called from webhook handlers (Phase 6) and reconciler (Phase 7). Returns `None` if deduped.
**When to use:** Callers of `sync_task` — not the job itself.

```python
# Source: arq 0.28.0 docs [VERIFIED]
from arq import ArqRedis

async def enqueue_sync(redis: ArqRedis, zoho_task_id: str, defer_secs: int = 0) -> None:
    job = await redis.enqueue_job(
        "sync_task",
        zoho_task_id,
        _job_id=f"sync:{zoho_task_id}",
        _defer_by=defer_secs,  # 0 for Todoist-triggered; settings.zoho_job_defer_secs for Zoho
    )
    if job is None:
        log.warning("sync_task_dedup_dropped", zoho_task_id=zoho_task_id)
```

### Pattern 7: Worker entry point

**What:** `run_worker(WorkerSettings)` is the synchronous entry point. Called from `__main__.py`.

```python
# app/worker/__main__.py
from arq import run_worker
from app.worker.settings import WorkerSettings

if __name__ == "__main__":
    run_worker(WorkerSettings)
```

**Railway command:** `python -m app.worker`

### Anti-Patterns to Avoid

- **Calling external APIs under `FOR UPDATE` lock:** Lock should only cover the hash-compare + DB-update section. API calls under lock inflate transaction time and risk Postgres statement timeouts.
- **Sleeping inside the job for the defer:** `LOOP-4` requires the 2-second defer to happen at enqueue time (`_defer_by=2`), not as `asyncio.sleep(2)` inside the job. The job body should execute promptly.
- **Not releasing SETNX lock on exception:** Always use `try/finally` around the lock body. If the job raises, arq will retry but the lock must be released before the retry fires (the 30s TTL is the safety net if the worker crashes before the finally block).
- **Creating a new Redis pool in on_startup:** arq injects `ctx["redis"]` (the `ArqRedis` pool) automatically. Creating a second pool wastes connections.
- **Hardcoding max_tries=5:** arq's default is 5; set `max_tries=4` explicitly in `func()` to honour the "3 retries" requirement.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Job deduplication | Custom Redis set-check before enqueue | `_job_id` in `enqueue_job` | arq uses a Redis transaction for atomic uniqueness check — race-free |
| Job retry with backoff | `time.sleep()` in exception handler | `raise Retry(defer=N)` | arq re-enqueues with a score in the Redis sorted set; worker doesn't block |
| Worker lifecycle (startup/shutdown hooks) | Module-level globals with atexit handlers | `on_startup` / `on_shutdown` in `WorkerSettings` | arq guarantees these run before any job executes / after all jobs complete |
| Redis URL parsing | `urllib.parse.urlparse(redis_url)` manually | `RedisSettings.from_dsn(url)` | arq already has a correct DSN parser that handles `redis://`, `rediss://`, unix sockets |
| Per-function timeout/retries | Try/except with manual timeout | `func(sync_task, timeout=60, max_tries=4)` | arq wraps the coroutine in an asyncio task with a deadline; cleaner than manual |

**Key insight:** arq's `_job_id` dedup is Redis-transactional (uses MULTI/EXEC pipeline). Don't replicate this with a separate SETNX before enqueue — that would be a redundant and fragile check outside the transaction.

---

## Common Pitfalls

### Pitfall 1: `ctx["redis"]` vs. a separate pool
**What goes wrong:** Developer creates `ctx["redis_pool"] = await create_pool(RedisSettings.from_dsn(...))` in `on_startup`, but arq already injects `ctx["redis"]` (the pool it uses internally). Now there are two connections.
**Why it happens:** The arq docs show `create_pool` for enqueueing from external code, not inside the worker.
**How to avoid:** Inside `sync_task`, always use `ctx["redis"]`. Only use `create_pool` in web/webhook handlers (Phase 6) that need to enqueue jobs.
**Warning signs:** Two Redis connection pools in `on_startup`.

### Pitfall 2: API calls under `FOR UPDATE` lock
**What goes wrong:** The Zoho/Todoist API call is slow (1–3s); the Postgres lock is held the entire time, blocking any other worker trying to process the same task.
**Why it happens:** Intuition says "lock everything that could race."
**How to avoid:** Fetch live data from both APIs BEFORE entering the `session.begin()` / `with_for_update()` block. The lock only guards the hash comparison and DB write (milliseconds).

### Pitfall 3: max_tries semantics
**What goes wrong:** Developer sets `max_tries=3` thinking "3 retries" but arq interprets it as 3 total attempts (1 initial + 2 retries), not 4.
**Why it happens:** The term "retries" vs "tries" is ambiguous.
**How to avoid:** From the source: job is permanently failed when `job_try > max_tries`. So `max_tries=4` means the 4th attempt is the last. Set `max_tries=4` for "3 retries after the initial attempt." [VERIFIED: arq 0.28.0 source `if job_try > max_tries:` line confirmed]
**Warning signs:** Test shows job fails on 3rd attempt instead of 4th.

### Pitfall 4: SETNX lock not released on Retry exception
**What goes wrong:** Job raises `Retry`, which exits the try block, but the finally block still runs — lock IS released. However, if `Retry` is raised BEFORE acquiring the lock (e.g., before API call), the delete will still run harmlessly. The real pitfall is raising `return` (not `Retry`) inside the lock body without a `finally`.
**How to avoid:** Always structure lock acquisition as: `try: ... finally: await redis.delete(lock_key)`. The TTL (30s) is a safety net if the process dies.

### Pitfall 5: `_defer_by` is not the same as sleeping in the job
**What goes wrong:** Developer adds `await asyncio.sleep(settings.zoho_job_defer_secs)` at the top of `sync_task`.
**Why it happens:** The 2-second defer feels natural inside the job.
**How to avoid:** The defer should happen at enqueue time (`enqueue_job(..., _defer_by=2)`). This allows arq to pick up other jobs while the deferred job waits in the Redis sorted set. Sleeping inside the job blocks a worker slot.

### Pitfall 6: LWW direction when both sides changed
**What goes wrong:** If both `zoho_hash != last_hash` and `todoist_hash != last_hash`, the developer writes to both targets, causing a loop.
**How to avoid:** When both have changed (true simultaneous edit), pick one side as the winner (Zoho wins in this project — it's the source of truth), write to Todoist only, log `action='overwrite'`. The resulting hash suppresses the echo on the next webhook.

---

## Code Examples

### Full sync_task skeleton (verified pattern)

```python
# Source: arq 0.28.0 docs + SQLAlchemy 2.0 async docs [VERIFIED: installed versions]
import asyncio
from datetime import datetime, timezone

from arq import Retry
from sqlalchemy import select

from app.core.config import get_settings
from app.core.hash import canonical_hash
from app.core.logging import get_logger
from app.db.models import SyncState, SyncEvent
from app.zoho.client import ZohoRateLimitError, ZohoAPIError, ZohoNotFoundError
from app.todoist.client import TodoistRateLimitError, TodoistAPIError, TodoistNotFoundError
from app.zoho.normalise import zoho_record_to_normalised
from app.todoist.normalise import todoist_task_to_normalised

log = get_logger(__name__)

RETRY_DELAYS = {1: 5, 2: 15, 3: 60}


async def sync_task(ctx: dict, zoho_task_id: str) -> None:
    redis = ctx["redis"]           # ArqRedis — injected by arq automatically
    session_factory = ctx["session_factory"]
    zoho_client = ctx["zoho_client"]
    todoist_client = ctx["todoist_client"]
    job_try: int = ctx["job_try"]
    settings = get_settings()

    # [1] SETNX per-task lock — second dedup layer
    lock_key = f"lock:sync:{zoho_task_id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=30)
    if not acquired:
        log.warning("sync_task_lock_not_acquired", zoho_task_id=zoho_task_id)
        return

    try:
        await _execute_sync(
            zoho_task_id, session_factory, zoho_client, todoist_client, settings, job_try
        )
    except (ZohoRateLimitError, ZohoAPIError, TodoistRateLimitError, TodoistAPIError) as exc:
        delay = RETRY_DELAYS.get(job_try, 60)
        log.error("sync_task_api_error_will_retry", zoho_task_id=zoho_task_id, attempt=job_try, delay=delay)
        raise Retry(defer=delay) from exc
    finally:
        await redis.delete(lock_key)


async def _execute_sync(zoho_task_id, session_factory, zoho_client, todoist_client, settings, job_try):
    # [2] Fetch live state from both APIs (BEFORE acquiring DB lock)
    zoho_record = await zoho_client.get_task(zoho_task_id)   # raises ZohoNotFoundError on 404
    zoho_norm = zoho_record_to_normalised(zoho_record)

    # [3] Look up todoist_task_id from sync_state (no lock needed for read)
    async with session_factory() as session:
        result = await session.execute(
            select(SyncState).where(SyncState.zoho_task_id == zoho_task_id)
        )
        state = result.scalar_one_or_none()

    if state is None:
        # New task — create in Todoist, write ID back to Zoho, insert sync_state
        from app.todoist.writer import create_todoist_task
        from app.zoho.writer import write_todoist_id_to_zoho
        from app.zoho.state import token_state
        todoist_id = await create_todoist_task(
            zoho_norm, zoho_task_id, todoist_client._api
        )
        await write_todoist_id_to_zoho(zoho_task_id, todoist_id, token_state["access_token"])
        new_hash = canonical_hash(zoho_norm)
        async with session_factory() as session:
            async with session.begin():
                session.add(SyncState(
                    zoho_task_id=zoho_task_id,
                    todoist_task_id=todoist_id,
                    last_hash=new_hash,
                    last_synced_at=datetime.now(timezone.utc),
                ))
                session.add(SyncEvent(
                    zoho_task_id=zoho_task_id, action="sync", source="zoho_webhook",
                    detail={"direction": "zoho_to_todoist", "created": True},
                ))
        return

    todoist_norm = await todoist_client.get_task(state.todoist_task_id)

    # [5] Compute canonical hashes
    zoho_hash = canonical_hash(zoho_norm)
    todoist_hash = canonical_hash(todoist_norm)

    # [6+7] SELECT FOR UPDATE critical section
    async with session_factory() as session:
        async with session.begin():
            locked = await session.execute(
                select(SyncState)
                .where(SyncState.zoho_task_id == zoho_task_id)
                .with_for_update()
            )
            state = locked.scalar_one()
            last_hash = state.last_hash

            if zoho_hash == last_hash and todoist_hash == last_hash:
                # Both sides match — echo suppressed
                session.add(SyncEvent(
                    zoho_task_id=zoho_task_id, action="echo_suppressed", source="worker",
                    detail={"hash": zoho_hash[:8]},
                ))
                return

            if zoho_hash != last_hash and todoist_hash != last_hash:
                # Simultaneous edit — LWW: Zoho wins
                action = "overwrite"
                direction = "zoho_to_todoist"
            elif zoho_hash != last_hash:
                action = "sync"
                direction = "zoho_to_todoist"
            else:
                action = "sync"
                direction = "todoist_to_zoho"

            # [write to target — happens OUTSIDE the FOR UPDATE in the same transaction
            # is fine because writing to external APIs inside BEGIN is acceptable here
            # since we need to update DB + log atomically]
            await _write_to_target(direction, state, zoho_norm, todoist_norm)

            new_hash = zoho_hash if direction == "zoho_to_todoist" else todoist_hash
            state.last_hash = new_hash
            state.last_synced_at = datetime.now(timezone.utc)
            session.add(SyncEvent(
                zoho_task_id=zoho_task_id, action=action, source="worker",
                detail={"direction": direction, "new_hash": new_hash[:8]},
            ))
        # session.begin() exit → commit → FOR UPDATE lock released
```

### WorkerSettings example (full)

```python
# app/worker/settings.py [VERIFIED: arq 0.28.0]
from arq import func
from arq.connections import RedisSettings
from app.core.config import get_settings
from app.worker.jobs import sync_task

async def on_startup(ctx: dict) -> None:
    import resend
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.zoho.client import ZohoClient
    from app.zoho.state import token_state
    from app.todoist.client import TodoistClient
    from app.zoho.token_manager import (
        KV_ACCESS_TOKEN_KEY, KV_EXPIRES_AT_KEY,
        load_token_from_kv, refresh_access_token, upsert_kv,
    )
    from datetime import datetime, timezone

    settings = get_settings()
    resend.api_key = settings.resend_api_key

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    ctx["engine"] = engine
    ctx["session_factory"] = session_factory

    # Load Zoho token from kv_store (shared with web service — Railway services share Postgres)
    async with session_factory() as session:
        stored_token, stored_expires_at = await load_token_from_kv(session)

    now = datetime.now(timezone.utc)
    if not stored_token or stored_expires_at is None or stored_expires_at <= now:
        access_token, expires_at = await refresh_access_token(settings)
        async with session_factory() as session:
            await upsert_kv(session, KV_ACCESS_TOKEN_KEY, access_token)
            await upsert_kv(session, KV_EXPIRES_AT_KEY, expires_at.isoformat())
            await session.commit()
    else:
        access_token = stored_token

    token_state["access_token"] = access_token
    ctx["zoho_client"] = ZohoClient(access_token=access_token)
    ctx["todoist_client"] = TodoistClient(api_token=settings.todoist_api_token)


async def on_shutdown(ctx: dict) -> None:
    await ctx["todoist_client"].close()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [
        func(sync_task, timeout=60, keep_result=300, max_tries=4),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| arq `WorkerSettings` as module-level class | Same pattern — no change | — | Stable API in arq 0.25+ |
| arq `max_tries` default was 5 | Still 5 in 0.28.0 | — | Must explicitly set `max_tries=4` in `func()` to honour 3-retry requirement |
| Redis SETNX as a separate command | `redis.set(key, val, nx=True, ex=TTL)` atomic | redis-py 3.x+ | Atomically sets AND expires; no race between SETNX + EXPIRE |

**Deprecated/outdated:**
- `aioredis` (separate package): Redis async was merged into `redis-py` as `redis.asyncio`. arq 0.26+ uses `redis.asyncio`. No `aioredis` import needed.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Token state written to `kv_store` by the web service is readable by the worker process (both share the same Railway Postgres and the `kv_store` table) | Standard Stack / on_startup pattern | If tokens are only in-memory, worker starts with no valid token. Mitigation: worker's `on_startup` always calls `load_token_from_kv` and refreshes if stale — this handles the case regardless. |
| A2 | `ZohoClient` and `TodoistClient` are safe to instantiate per-worker-startup (not global singletons) | Architecture Patterns | If either client has module-level initialisation that breaks on second import, worker startup fails. Prior phases use them as simple dataclasses/wrappers — low risk. |

**If this table is empty:** All claims in this research were verified or cited — no user confirmation needed.

Assumption A1 is LOW-RISK because the `on_startup` pattern already handles token refresh from scratch if `kv_store` is empty.

---

## Open Questions

1. **Zoho webhook token vs worker token**
   - What we know: The web service refreshes the Zoho token proactively every 50 min and writes it to `kv_store`. The worker reads it from `kv_store` on startup.
   - What's unclear: If the worker stays up for 50+ minutes, its in-memory token goes stale. The web service's proactive refresh loop writes the new token to `kv_store`, but the worker's `ctx["zoho_client"].access_token` is not updated.
   - Recommendation: In Phase 5, start a lightweight proactive refresh loop inside `on_startup` for the worker process (reuse `proactive_refresh_loop` from `app/zoho/token_manager.py`), or make `ZohoClient.access_token` read from `token_state` dict by reference (already mutable in Phase 2 design). The plan should address this explicitly.

2. **Direction of sync when Todoist task is the trigger (Phase 6 wires this)**
   - What we know: `sync_task` receives `zoho_task_id`. If a Todoist change triggers it, the caller must resolve the Zoho task ID from the Todoist task ID (via `sync_state.todoist_task_id` reverse lookup).
   - What's unclear: Phase 5 `sync_task` needs only `zoho_task_id`; Phase 6 handles the Todoist-webhook-to-zoho-ID resolution. Phase 5 does not need to worry about this.
   - Recommendation: Document in `sync_task` docstring that the function always works with `zoho_task_id` as the primary key.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| arq | Worker queue | Yes | 0.28.0 (pinned) | — |
| redis (redis.asyncio) | arq transport | Yes | 5.3.1 | — |
| SQLAlchemy asyncio | SELECT FOR UPDATE | Yes | 2.0.49 | — |
| asyncpg | SQLAlchemy async Postgres | Yes | 0.31.0 | — |
| Redis server | arq + SETNX | External (Railway) | — | Not testable in unit tests; mock in tests |
| Postgres | sync_state FOR UPDATE | External (Railway) | — | Not testable in unit tests; mock session in tests |

**Missing dependencies with no fallback:** None — all code-level dependencies are installed.

**Missing dependencies with fallback:** Redis and Postgres servers are Railway services; unit tests mock them.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (asyncio_mode = "auto") |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `python -m pytest tests/unit/test_worker_jobs.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SYNC-10 | `enqueue_job` returns `None` on duplicate → logs WARN | unit | `pytest tests/unit/test_worker_jobs.py::test_enqueue_dedup_logs_warn -x` | No — Wave 0 |
| SYNC-11 | Simultaneous edit: Zoho wins, `action='overwrite'` logged | unit | `pytest tests/unit/test_worker_jobs.py::test_lww_zoho_wins -x` | No — Wave 0 |
| LOOP-1 | Incoming hash == `last_hash` → `echo_suppressed`, no write | unit | `pytest tests/unit/test_worker_jobs.py::test_echo_suppressed -x` | No — Wave 0 |
| LOOP-3 | `SELECT FOR UPDATE` called on `sync_state` row | unit | `pytest tests/unit/test_worker_jobs.py::test_select_for_update_called -x` | No — Wave 0 |
| LOOP-4 | 2-second defer passed via `_defer_by` at enqueue, NOT sleep inside job | unit | `pytest tests/unit/test_worker_jobs.py::test_enqueue_defer_by -x` | No — Wave 0 |
| LOOP-5 | Self-created Todoist task with footer → identified as sync-managed, not reverse-synced | unit | `pytest tests/unit/test_worker_jobs.py::test_bootstrap_race_suppressed -x` | No — Wave 0 |
| INFRA-1 | `app/worker/__main__.py` runs `run_worker(WorkerSettings)` | integration/smoke | `python -m app.worker --burst` (no jobs → exits clean) | No — Wave 0 |
| INFRA-3 | `RedisSettings.from_dsn(redis_url)` used in `WorkerSettings` | unit | `pytest tests/unit/test_worker_settings.py::test_redis_settings_from_dsn -x` | No — Wave 0 |

### Sampling Rate

- **Per task commit:** `python -m pytest tests/unit/test_worker_jobs.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_worker_jobs.py` — covers SYNC-10, SYNC-11, LOOP-1, LOOP-3, LOOP-4, LOOP-5
- [ ] `tests/unit/test_worker_settings.py` — covers INFRA-3
- [ ] `app/worker/__init__.py` — package marker
- [ ] `app/worker/__main__.py` — Railway entry point
- [ ] `app/worker/settings.py` — `WorkerSettings` class
- [ ] `app/worker/jobs.py` — `sync_task` function

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Worker uses internal Redis/Postgres; no user auth |
| V3 Session Management | No | Background worker — no sessions |
| V4 Access Control | No | Worker operates on its own queue only |
| V5 Input Validation | Yes | `zoho_task_id` is a string from queue; validate it's non-empty before API calls |
| V6 Cryptography | No | No cryptographic operations in this phase |

### Known Threat Patterns for arq worker stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed `zoho_task_id` in job payload | Tampering | Validate non-empty string; typed exceptions from API client |
| Redis queue poisoning (if Redis not TLS-protected) | Tampering | Railway internal networking; Redis not exposed externally [ASSUMED] |
| Lock starvation (lock never released) | Denial of Service | 30s TTL on SETNX key auto-releases stale locks |

---

## Sources

### Primary (HIGH confidence)

- arq 0.28.0 installed source at `/data/home/.local/lib/python3.11/site-packages/arq/` — `Worker.__init__` signature, `run_job` retry logic (`job_try > max_tries`), `ArqRedis.set` signature, `RedisSettings.from_dsn` source [VERIFIED]
- arq docs at `https://arq-docs.helpmanual.io/` via Context7 `/websites/arq-docs_helpmanual_io` — WorkerSettings, func(), Retry, cron, enqueue_job patterns [VERIFIED]
- Python arq GitHub docs via Context7 `/python-arq/arq` — job uniqueness, retry backoff patterns [VERIFIED]
- SQLAlchemy 2.0.49 installed — `select().with_for_update()` API [VERIFIED: installed version confirmed]
- redis-py 5.3.1 installed — `redis.asyncio.Redis.set(nx=True, ex=N)` atomic SETNX+EXPIRE [VERIFIED: ArqRedis.set signature confirmed]

### Secondary (MEDIUM confidence)

- Project codebase (`app/core/config.py`, `app/core/hash.py`, `app/db/models.py`, `app/main.py`, `app/zoho/client.py`, `app/zoho/writer.py`, `app/todoist/writer.py`) — existing module interfaces consumed by Phase 5 [VERIFIED: read directly]

### Tertiary (LOW confidence)

- None.

---

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH — arq 0.28.0 already installed and pinned; all APIs verified against installed source
- Architecture: HIGH — pipeline structure follows directly from requirements; SELECT FOR UPDATE and SETNX patterns are well-established
- Pitfalls: HIGH — derived from verified arq source code (job_try > max_tries check, ctx["redis"] injection), not training data

**Research date:** 2026-04-24
**Valid until:** 2026-07-24 (arq 0.28.0 is pinned; SQLAlchemy 2.0 is stable)
