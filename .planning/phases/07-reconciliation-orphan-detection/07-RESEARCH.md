# Phase 7: Reconciliation & Orphan Detection - Research

**Researched:** 2026-04-24
**Domain:** arq cron scheduling, periodic sweep architecture, orphan detection, kv_store pattern, sync_token persistence
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SEED-5 | Reconciliation cron every 15 min: query Zoho modified last 20 min + Todoist incremental delta; enqueue sync jobs for hash mismatches; update sync_token after each poll. | arq `cron()` with `minute={0,15,30,45}` confirmed working. `fetch_tasks_modified_since` and `fetch_sync_delta` already implemented and reusable. |
| SEED-6 | Orphan sweep hourly: for all sync_state rows, verify Zoho task exists + assigned to me, and Todoist task exists; two-cycle confirmation before orphan handling. | `sync_state.orphan_check_count` column already in schema. ZohoClient.get_task + TodoistClient.fetch_todoist_task reusable. |
| SEED-7 | sync_token persists in kv_store; loaded on restart for incremental sync; falls back to '*' on missing/corruption. | `load_sync_token` / `save_sync_token` already implemented in `app/todoist/sync_manager.py`. Reconciler reuses these directly. |
| LOOP-3 | SELECT FOR UPDATE on sync_state row at start of critical section. | sync_task already does this. Reconciler calls `enqueue_sync` — the lock is in the job, not the cron sweep itself. Sweep is read-only; job carries the lock. |
| EDGE-1 | Task reassigned away from me in Zoho: delete Todoist task, send Resend email, remove sync_state row, log `action='orphan'`. | Already implemented in Phase 4 (`delete_todoist_task` + `send_deletion_notification`). Orphan sweep calls these after two-cycle confirmation. |
| EDGE-2 | Todoist task deleted by user: delete Zoho task, send Resend email, remove sync_state row, log `action='orphan'`. | Already implemented in Phase 4 (`delete_zoho_task` + `send_deletion_notification`). Orphan sweep handles via `TodoistNotFoundError`. |
| EDGE-5 | Two-cycle confirmation before orphan handling. Single 404 → increment `orphan_check_count`, log WARN. Second consecutive 404 → orphan handling. | `sync_state.orphan_check_count` tracks count. Reset to 0 on successful verification. |
| EDGE-6 | Resend failure does not roll back deletion. Log error, continue. | Already implemented in `send_deletion_notification` (EDGE-6 guard in place). |
| EDGE-8 | Missing `[zoho:ID]` footer on Todoist task that has a sync_state row: reconciler detects missing link and re-attaches footer via update. | Reconciler fetches Todoist task description, checks footer via `extract_zoho_id`, calls `update_todoist_task` with re-injected footer if missing. |
| SYNC-10 | arq job dedup: `_job_id=f"sync:{zoho_task_id}"`. Duplicate dropped with WARN log. | `enqueue_sync()` already handles dedup. Reconciler calls `enqueue_sync()` — same dedup applies. |

</phase_requirements>

---

## Summary

Phase 7 adds two periodic sweeps to the arq worker that make the system self-healing. The **reconciliation cron** (every 15 minutes) catches missed webhooks by fetching Zoho tasks modified in the last 20 minutes and the Todoist incremental delta, then enqueuing `sync_task` for any task with a hash mismatch. The **orphan sweep** (hourly) scans all `sync_state` rows and verifies both sides still exist and the Zoho task is still assigned to me, applying a two-cycle confirmation before deleting orphaned tasks.

All the heavy lifting is already done: `fetch_tasks_modified_since`, `fetch_sync_delta`, `load_sync_token`/`save_sync_token`, `enqueue_sync`, `delete_todoist_task`, `delete_zoho_task`, `send_deletion_notification`, and the `sync_state.orphan_check_count` schema column are all implemented and available. Phase 7 is primarily an orchestration and wiring phase — the reconciler functions coordinate existing primitives rather than implementing new business logic.

The reconciler code belongs in a new `app/worker/reconciler.py` module. The two cron jobs (`reconcile_sweep` and `orphan_sweep`) are registered in `WorkerSettings.cron_jobs` using `arq.cron()` with parameter-based scheduling (arq 0.28.0 does NOT support crontab strings). Two `kv_store` keys (`reconciler_last_run` and `orphan_sweep_last_run`) are written after each sweep and read by the Phase 8 `/health` endpoint.

There is one pre-existing naming mismatch to resolve: `jobs.py` calls `todoist_client.get_task()` but `TodoistClient` defines `fetch_todoist_task()`. The reconciler must use `fetch_todoist_task()` (the correct method name). Wave 0 should add a `get_task` alias to `TodoistClient` or fix the call site in `jobs.py`.

**Primary recommendation:** New `app/worker/reconciler.py` with two async functions registered as arq cron jobs in `WorkerSettings`. Reconciler calls existing primitives — no new API client methods, no new DB columns, no new writer functions needed.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Reconciliation cron scheduling | Worker (arq cron) | — | arq `WorkerSettings.cron_jobs` drives the schedule |
| Zoho modified-since fetch | Worker (reconciler) | Zoho API | `ZohoClient.fetch_tasks_modified_since` already handles pagination and criteria |
| Todoist incremental delta fetch | Worker (reconciler) | Todoist Sync API | `TodoistClient.fetch_sync_delta` + `load/save_sync_token` already implemented |
| Hash mismatch detection | Worker (reconciler) | Database | Load `sync_state.last_hash`, compute canonical_hash on fresh fetch, compare |
| Job enqueue from reconciler | Worker (reconciler) → Redis | — | `enqueue_sync(ctx["redis"], zoho_task_id)` — same helper as webhook handlers |
| sync_token persistence | Database / Storage (kv_store) | — | `save_sync_token` persists after each delta poll |
| Orphan sweep scan | Worker (reconciler) | Database | SELECT all sync_state rows; batch API checks |
| Zoho 404 / reassignment check | Worker (reconciler) | Zoho API | `get_task` returns record; check `Owner.id == ZOHO_USER_ID` |
| Todoist 404 check | Worker (reconciler) | Todoist API | `fetch_todoist_task` raises `TodoistNotFoundError` on 404 |
| Two-cycle orphan confirmation | Database / Storage | Worker | Increment `orphan_check_count` on first 404; act on second |
| Footer re-attachment (EDGE-8) | Worker (reconciler) | Todoist API | Detect missing `[zoho:ID]`, call `update_todoist_task` with re-injected footer |
| Sweep timestamps (last_run) | Database / Storage (kv_store) | — | Written after each sweep; read by `/health` in Phase 8 |
| Deletion + notification | Worker (reconciler) → Resend | — | Reuse `delete_todoist_task`, `delete_zoho_task`, `send_deletion_notification` |

---

## Standard Stack

### Core (all already pinned — no new dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| arq | 0.28.0 | `cron()` for scheduling, `ctx["redis"]` for enqueue | Already used; cron API confirmed [VERIFIED: arq.__version__] |
| SQLAlchemy[asyncio] | 2.0.49 | SELECT sync_state rows, UPDATE orphan_check_count | Already used |
| app.zoho.client | — | `fetch_tasks_modified_since`, `get_task` | Already implemented in Phase 2 |
| app.todoist.client | — | `fetch_sync_delta`, `fetch_todoist_task` | Already implemented in Phase 3 |
| app.todoist.sync_manager | — | `load_sync_token`, `save_sync_token` | Already implemented in Phase 3 |
| app.worker.enqueue | — | `enqueue_sync` helper with dedup | Already implemented in Phase 5 |
| app.todoist.writer | — | `delete_todoist_task`, `update_todoist_task` | Already implemented in Phase 4 |
| app.zoho.writer | — | `delete_zoho_task` | Already implemented in Phase 4 |
| app.core.notifications | — | `send_deletion_notification` | Already implemented in Phase 4 |
| app.core.hash | — | `canonical_hash` | Already implemented in Phase 1 |

**No new pip dependencies required for Phase 7.** [VERIFIED: requirements.txt]

### kv_store Keys for Phase 7

| Key | Written By | Read By | Purpose |
|-----|-----------|---------|---------|
| `todoist_sync_token` | `save_sync_token` (existing) | `load_sync_token` (existing) | Incremental delta continuation |
| `reconciler_last_run` | reconciler after each 15-min sweep | `/health` endpoint (Phase 8) | Staleness detection (flag degraded if >25 min old) |
| `orphan_sweep_last_run` | reconciler after each hourly sweep | `/health` endpoint (Phase 8) | Optional observability |

---

## Architecture Patterns

### System Architecture Diagram

```
arq Worker Process
├── cron: reconcile_sweep (every 15 min)
│   ├── [1] fetch_tasks_modified_since(now - 20min, owner_id)  → Zoho API
│   ├── [2] load_sync_token(session) → kv_store
│   ├── [3] fetch_sync_delta(sync_token, project_id)           → Todoist Sync API
│   ├── [4] save_sync_token(session, new_token)                → kv_store
│   ├── [5] for each zoho_task: load sync_state.last_hash      → DB
│   │       compute canonical_hash(zoho_record)
│   │       if mismatch → enqueue_sync(redis, zoho_task_id)    → Redis
│   ├── [6] for each todoist_item: extract_zoho_id(description)
│   │       if zoho_id: load sync_state.last_hash              → DB
│   │       fetch zoho_norm (or use existing fetch) → hash compare
│   │       if mismatch → enqueue_sync(redis, zoho_task_id)    → Redis
│   └── [7] upsert_kv("reconciler_last_run", now)              → kv_store
│
└── cron: orphan_sweep (every hour)
    ├── [1] SELECT all sync_state rows                         → DB
    ├── [2] for each row:
    │       try: get_task(zoho_task_id)                        → Zoho API
    │           if Owner.id != ZOHO_USER_ID → treat as 404 (reassigned)
    │       except ZohoNotFoundError → 404
    │       try: fetch_todoist_task(todoist_task_id)           → Todoist API
    │       except TodoistNotFoundError → 404
    │
    │       First 404: orphan_check_count += 1, log WARN
    │       Second 404: orphan handling
    │           Zoho missing/reassigned → delete_todoist_task + send_deletion_notification
    │           Todoist missing → delete_zoho_task + send_deletion_notification
    │           DELETE sync_state row + log action='orphan'
    │
    │       Successful verification: orphan_check_count = 0
    └── [3] upsert_kv("orphan_sweep_last_run", now)            → kv_store
```

### Recommended Project Structure

```
app/worker/
├── reconciler.py        # NEW: reconcile_sweep() + orphan_sweep() cron functions
├── settings.py          # MODIFY: add cron_jobs = [...] class attribute
├── jobs.py              # NO CHANGE (sync_task stays here)
├── enqueue.py           # NO CHANGE (enqueue_sync reused as-is)
└── __main__.py          # NO CHANGE
```

### Pattern 1: arq Cron Registration (VERIFIED)

arq 0.28.0 does **not** support crontab strings like `"*/15 * * * *"`. The `cron()` function
takes keyword parameters for each time unit. `minute` accepts an `int` or a `set[int]`.

```python
# Source: arq 0.28.0 cron() signature — verified via inspect.getsource
from arq import cron
from app.worker.reconciler import reconcile_sweep, orphan_sweep

class WorkerSettings:
    functions = [
        func(sync_task, timeout=60, keep_result=300, max_tries=4),
    ]
    cron_jobs = [
        cron(reconcile_sweep, minute={0, 15, 30, 45}, second=0, timeout=300),
        cron(orphan_sweep,    minute=0,               second=0, timeout=600),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
```

`WorkerSettings.cron_jobs` is picked up by `get_kwargs()` which reads `settings_cls.__dict__`
and passes matching Worker.__init__ parameter names through. `cron_jobs` is a valid
`Worker.__init__` parameter. [VERIFIED: arq.worker.get_kwargs source]

### Pattern 2: Reconcile Sweep Structure

```python
# app/worker/reconciler.py
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db.models import SyncState
from app.todoist.sync_manager import load_sync_token, save_sync_token
from app.worker.enqueue import enqueue_sync
from app.core.hash import canonical_hash
from app.zoho.normalise import zoho_record_to_normalised
from app.zoho.token_manager import upsert_kv

KV_RECONCILER_LAST_RUN = "reconciler_last_run"
KV_ORPHAN_SWEEP_LAST_RUN = "orphan_sweep_last_run"
RECONCILE_LOOKBACK_MINUTES = 20

async def reconcile_sweep(ctx: dict) -> None:
    """15-min reconciliation cron. SEED-5."""
    session_factory = ctx["session_factory"]
    zoho_client = ctx["zoho_client"]
    todoist_client = ctx["todoist_client"]
    redis = ctx["redis"]
    settings = get_settings()

    since = datetime.now(timezone.utc) - timedelta(minutes=RECONCILE_LOOKBACK_MINUTES)

    # [1] Zoho side: tasks modified in last 20 min
    zoho_records = await zoho_client.fetch_tasks_modified_since(since, settings.zoho_user_id)

    async with session_factory() as session:
        for record in zoho_records:
            zoho_task_id = str(record.get("id"))
            zoho_norm = zoho_record_to_normalised(record)
            zoho_hash = canonical_hash(zoho_norm)
            result = await session.execute(
                select(SyncState).where(SyncState.zoho_task_id == zoho_task_id)
            )
            state = result.scalar_one_or_none()
            if state is None or state.last_hash != zoho_hash:
                await enqueue_sync(redis, zoho_task_id, defer_secs=0)

    # [2] Todoist side: incremental delta
    async with session_factory() as session:
        sync_token = await load_sync_token(session)

    items, new_token = await todoist_client.fetch_sync_delta(
        sync_token=sync_token,
        project_id=settings.todoist_project_id,
    )

    async with session_factory() as session:
        await save_sync_token(session, new_token)

    for item in items:
        if item.get("is_deleted"):
            continue
        zoho_id = extract_zoho_id(item.get("description"))
        if zoho_id is None:
            continue
        await enqueue_sync(redis, zoho_id, defer_secs=0)

    # [3] Update last_run timestamp
    async with session_factory() as session:
        await upsert_kv(session, KV_RECONCILER_LAST_RUN, datetime.now(timezone.utc).isoformat())
        await session.commit()
```

### Pattern 3: Orphan Sweep — Two-Cycle Confirmation (EDGE-5)

The `orphan_check_count` column in `sync_state` is already in the schema.

```python
async def orphan_sweep(ctx: dict) -> None:
    """Hourly orphan sweep. SEED-6, EDGE-1, EDGE-2, EDGE-5."""
    session_factory = ctx["session_factory"]
    zoho_client = ctx["zoho_client"]
    todoist_client = ctx["todoist_client"]
    settings = get_settings()

    async with session_factory() as session:
        result = await session.execute(select(SyncState))
        rows = result.scalars().all()

    for state in rows:
        zoho_missing = False
        todoist_missing = False

        # --- Check Zoho side ---
        try:
            record = await zoho_client.get_task(state.zoho_task_id)
            data = record.get("data", [{}])[0]
            owner_id = str((data.get("Owner") or {}).get("id", ""))
            if owner_id != settings.zoho_user_id:
                # Reassigned — treat same as 404 (EDGE-1)
                zoho_missing = True
        except ZohoNotFoundError:
            zoho_missing = True
        except (ZohoRateLimitError, ZohoAPIError):
            log.warning("orphan_sweep_zoho_api_error", zoho_task_id=state.zoho_task_id)
            continue  # Skip this row; will retry next hour

        # --- Check Todoist side ---
        try:
            await todoist_client.fetch_todoist_task(state.todoist_task_id)
        except TodoistNotFoundError:
            todoist_missing = True
        except (TodoistRateLimitError, TodoistAPIError):
            log.warning("orphan_sweep_todoist_api_error", todoist_task_id=state.todoist_task_id)
            continue

        is_orphan = zoho_missing or todoist_missing

        if not is_orphan:
            # Healthy — reset counter
            if state.orphan_check_count > 0:
                async with session_factory() as session:
                    async with session.begin():
                        locked = await session.get(SyncState, state.zoho_task_id)
                        locked.orphan_check_count = 0
            continue

        # First 404: increment count and warn
        if state.orphan_check_count < 1:
            log.warning("orphan_first_cycle", zoho_task_id=state.zoho_task_id,
                        zoho_missing=zoho_missing, todoist_missing=todoist_missing)
            async with session_factory() as session:
                async with session.begin():
                    locked = await session.get(SyncState, state.zoho_task_id)
                    locked.orphan_check_count += 1
            continue

        # Second consecutive 404: act
        await _handle_orphan(state, zoho_missing, todoist_missing, ctx, settings)
```

### Pattern 4: Orphan Handling (EDGE-1, EDGE-2, EDGE-6)

```python
async def _handle_orphan(state, zoho_missing, todoist_missing, ctx, settings):
    """Delete from live system, remove sync_state row, log, notify. EDGE-1/EDGE-2/EDGE-6."""
    session_factory = ctx["session_factory"]
    access_token = token_state["access_token"]

    if zoho_missing:
        # Zoho task gone/reassigned → delete Todoist counterpart (EDGE-1)
        try:
            await delete_todoist_task(state.todoist_task_id, ctx["todoist_client"]._api)
        except Exception as exc:
            log.error("orphan_todoist_delete_failed", error=str(exc))
    
    if todoist_missing:
        # Todoist task gone → delete Zoho counterpart (EDGE-2)
        try:
            await delete_zoho_task(state.zoho_task_id, access_token)
        except Exception as exc:
            log.error("orphan_zoho_delete_failed", error=str(exc))

    # Email notification (EDGE-6: failure does not roll back)
    await send_deletion_notification(
        subject="Sync orphan detected and resolved",
        html=f"<p>Orphan resolved: zoho={state.zoho_task_id}, "
             f"todoist={state.todoist_task_id}. "
             f"zoho_missing={zoho_missing}, todoist_missing={todoist_missing}</p>",
    )

    async with session_factory() as session:
        async with session.begin():
            row = await session.get(SyncState, state.zoho_task_id)
            if row:
                await session.delete(row)
            session.add(SyncEvent(
                zoho_task_id=state.zoho_task_id,
                action="orphan",
                source="reconciler",
                detail={"todoist_task_id": state.todoist_task_id,
                        "zoho_missing": zoho_missing,
                        "todoist_missing": todoist_missing},
            ))
```

### Pattern 5: Footer Re-attachment (EDGE-8)

When the orphan sweep finds a sync_state row where the Todoist task exists but its description
is missing the `[zoho:ID]` footer, the reconciler re-attaches it. This is a **reconciler path**,
not the orphan deletion path.

```python
# In orphan_sweep, after confirming Todoist task exists:
task = await todoist_client.fetch_todoist_task(state.todoist_task_id)
zoho_id = extract_zoho_id(task.description or "")
if zoho_id is None:
    log.warning("orphan_sweep_missing_footer", todoist_task_id=state.todoist_task_id)
    # Re-attach footer via REST update
    await todoist_client._api.update_task(
        state.todoist_task_id,
        description=(task.description or "") + f"\n\n---\n[zoho:{state.zoho_task_id}]",
    )
```

Note: `update_task` on `TodoistAPIAsync` accepts `description` as a kwarg. [ASSUMED — based on
todoist-api-python 4.0.0 pattern; verify against installed package before implementation.]

### Anti-Patterns to Avoid

- **Crontab strings in arq:** `cron(fn, "*/15 * * * *")` — arq 0.28.0 has no crontab string parser. Use `minute={0, 15, 30, 45}`. [VERIFIED]
- **Running sync logic in the cron function:** Reconciler only enqueues `sync_task` jobs via `enqueue_sync`. It does NOT replicate the sync pipeline. The job carries the SETNX lock and SELECT FOR UPDATE.
- **Fetching Zoho task inside the reconcile loop when not needed:** For Zoho-triggered reconciliation, the modified records are already in `zoho_records`. Only recompute hash from the already-fetched record, then enqueue — do NOT fetch again inside the loop.
- **Full sync_state table scan in reconcile_sweep:** Reconcile sweep works from modified records (Zoho API + Todoist delta), not from a table scan. Table scan is the orphan sweep only.
- **Calling `todoist_client.get_task()` instead of `fetch_todoist_task()`:** `jobs.py` line 133 calls `todoist_client.get_task()` which is NOT a defined method on `TodoistClient` (the defined method is `fetch_todoist_task`). Reconciler MUST use `fetch_todoist_task`. This mismatch should be resolved in Wave 0 (add alias or fix jobs.py call site).
- **Ignoring rate-limit errors in orphan sweep:** A rate-limit error means the API is alive but throttled — skip the row and retry next hour, do NOT count it as a 404.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Cron scheduling | Custom asyncio loop with `asyncio.sleep` | `arq.cron()` + `WorkerSettings.cron_jobs` | arq handles uniqueness across workers, startup alignment, timeout |
| Job deduplication | Custom Redis key per sweep | `enqueue_sync()` with `_job_id` | Already implemented with WARN log on drop |
| sync_token storage | New DB table or Redis key | `kv_store` via `load_sync_token`/`save_sync_token` | Already implemented and tested in Phase 3 |
| Last-run timestamps | New DB table | `kv_store` via `upsert_kv` | Same pattern as token storage; Phase 8 reads from kv_store |
| Orphan two-cycle state | Redis key or separate table | `sync_state.orphan_check_count` | Column already in schema, already defaulted to 0 |
| Delete + notify | Custom notification flow | `delete_todoist_task`/`delete_zoho_task` + `send_deletion_notification` | Already implemented with EDGE-6 guard |

---

## Common Pitfalls

### Pitfall 1: arq Cron — No Crontab String Support
**What goes wrong:** `cron(fn, "*/15 * * * *")` raises `TypeError` at import time — arq has no crontab parser.
**Why it happens:** arq uses explicit int/set parameters for time units, not crontab syntax.
**How to avoid:** Use `cron(fn, minute={0, 15, 30, 45}, second=0)` for 15-min intervals. [VERIFIED]
**Warning signs:** `TypeError: cron() takes 1 positional argument but 2 were given`

### Pitfall 2: Zoho Reassignment Not a 404
**What goes wrong:** A task reassigned away from the configured user still returns HTTP 200 from Zoho API — `ZohoNotFoundError` is never raised. The orphan sweep passes without acting.
**Why it happens:** Reassignment changes `Owner.id`, not the task's existence.
**How to avoid:** After a successful `get_task()` call, explicitly check `data[0]["Owner"]["id"] == settings.zoho_user_id`. Treat mismatch identically to `ZohoNotFoundError`. (EDGE-1 — the requirement explicitly calls out reassignment.)
**Warning signs:** `sync_state` rows accumulating for tasks that no longer appear in Zoho search results

### Pitfall 3: Todoist Delta Double-Enqueue with Zoho Sweep
**What goes wrong:** A task changed in Zoho appears in BOTH the Zoho modified-since list and the Todoist delta (because the previous sync wrote to Todoist). Two `enqueue_sync` calls are issued for the same zoho_task_id.
**Why it happens:** The reconciler fires both loops for the same task in the same sweep.
**How to avoid:** This is harmless — `enqueue_sync` dedup via `_job_id=f"sync:{zoho_task_id}"` drops the second enqueue with a WARN log (SYNC-10). No special handling needed, but the WARN log should be expected in tests.
**Warning signs:** Excessive `sync_task_dedup_dropped` log entries during reconciliation cycles

### Pitfall 4: orphan_check_count Not Reset on Recovery
**What goes wrong:** A task goes 404 on one sweep (count→1), then comes back (e.g., transient API error). Next sweep still sees count=1, so one more 404 triggers deletion of a live task.
**Why it happens:** Count is only incremented, never decremented.
**How to avoid:** Reset `orphan_check_count = 0` whenever a sweep finds BOTH sides healthy. [VERIFIED: schema has Integer column, nullable=False, default=0]
**Warning signs:** Unexpected orphan deletions for tasks that exist in both systems

### Pitfall 5: EDGE-8 Re-footer Overwrites User Description
**What goes wrong:** Re-attaching the footer appends to existing description. If the user deleted the footer to clean up the description, re-attachment restores it — which is the intended behaviour — but if description contains other user-edited content, blindly appending preserves user content correctly.
**Why it happens:** `description = existing_description + footer` is additive.
**How to avoid:** Use `(task.description or "") + f"\n\n---\n[zoho:{state.zoho_task_id}]"`. If description already ends with a footer for a DIFFERENT zoho_id (edge within an edge), log WARN and do not re-footer. Verify with `extract_zoho_id` first.
**Warning signs:** Duplicate `[zoho:ID]` footers in task descriptions

### Pitfall 6: Rate Limits from Bulk Orphan Sweep
**What goes wrong:** With many `sync_state` rows, the orphan sweep issues N Zoho + N Todoist API calls in rapid succession. Zoho rate-limits at 100 req/min per org for v8.
**Why it happens:** No rate limiting in the sweep loop.
**How to avoid:** Catch `ZohoRateLimitError` / `TodoistRateLimitError` per row, skip the row, continue. The next hourly sweep will retry. For v1 (likely small task count), this is acceptable. Add `asyncio.sleep(0.1)` between API calls if task count grows large.
**Warning signs:** Many `orphan_sweep_zoho_api_error` WARN logs in a single sweep cycle

### Pitfall 7: TodoistClient Method Name Mismatch
**What goes wrong:** `jobs.py` line 133 calls `todoist_client.get_task()`, but `TodoistClient` defines `fetch_todoist_task()`. In production this raises `AttributeError`. Tests pass because they mock the client.
**Why it happens:** Naming inconsistency between client.py and jobs.py.
**How to avoid:** Wave 0 of this phase must resolve this. Either add `get_task = fetch_todoist_task` alias to `TodoistClient`, or fix the call site in `jobs.py`. Reconciler should use `fetch_todoist_task()` to be correct.
**Warning signs:** `AttributeError: 'TodoistClient' object has no attribute 'get_task'` in production logs

---

## Code Examples

### How WorkerSettings cron_jobs is picked up by arq [VERIFIED]

```python
# arq/worker.py (arq 0.28.0) — how get_kwargs works:
def get_kwargs(settings_cls):
    worker_args = set(inspect.signature(Worker).parameters.keys())
    d = settings_cls.__dict__
    return {k: v for k, v in d.items() if k in worker_args}

# Worker.__init__ accepts `cron_jobs: Optional[Sequence[CronJob]] = None`
# So adding `cron_jobs = [...]` to WorkerSettings class body works correctly.
```

### 15-Minute Cron Registration [VERIFIED]

```python
from arq import cron
cron(reconcile_sweep, minute={0, 15, 30, 45}, second=0, timeout=300)
# Fires at :00, :15, :30, :45 of every hour
# timeout=300 = 5 minutes; sweep must complete within this window
```

### Hourly Cron Registration [VERIFIED]

```python
cron(orphan_sweep, minute=0, second=0, timeout=600)
# Fires once per hour at :00
# timeout=600 = 10 minutes; bulk API calls may take time
```

### upsert_kv for last_run tracking [VERIFIED — existing pattern from Phase 2]

```python
from app.zoho.token_manager import upsert_kv
async with session_factory() as session:
    await upsert_kv(session, "reconciler_last_run", datetime.now(timezone.utc).isoformat())
    await session.commit()
# upsert_kv does NOT auto-commit (Phase 2 contract) — caller must commit
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Crontab string scheduling | `arq.cron(fn, minute={...})` | arq 0.28.0 (released 2024) | No string parser; must use explicit params |
| Separate reconciler service | Cron jobs in existing arq worker | Phase 7 decision | No new Railway service needed |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `TodoistAPIAsync.update_task()` accepts `description` kwarg in todoist-api-python 4.0.0 | Pattern 5 (EDGE-8) | Footer re-attachment fails; need to check installed package API |
| A2 | Zoho API `get_task()` response structure: `{"data": [{"Owner": {"id": "..."}}, ...]}` | Pattern 3 (orphan sweep) | Owner check fails; need to verify against actual response shape from Phase 2 |
| A3 | 100 req/min Zoho API rate limit for EU v8 | Pitfall 6 | May be higher or lower; irrelevant for small task counts |

---

## Open Questions

1. **`TodoistAPIAsync.update_task()` signature for description parameter**
   - What we know: Phase 4 wrote `update_todoist_task` in `app/todoist/writer.py` — check there for the correct pattern
   - What's unclear: Does it accept `description` as a kwarg, or must it use a different field name?
   - Recommendation: Read `app/todoist/writer.py` in Wave 0; reuse the same call pattern for the footer re-attachment.

2. **Exact Zoho `get_task()` response shape for Owner field**
   - What we know: `zoho_record_to_normalised` parses Owner but only for is_assigned_to_me detection; check `app/zoho/normalise.py`
   - What's unclear: Is it `data[0]["Owner"]["id"]` or `data[0]["Owner_Id"]` or something else?
   - Recommendation: Read `app/zoho/normalise.py` in Wave 0 and use the same field path.

3. **Should reconcile_sweep also handle Todoist-deleted tasks (EDGE-2)?**
   - What we know: The Todoist Sync API delta includes `is_deleted: true` items (handled in `startup_sync` already)
   - What's unclear: Should the reconciler's Todoist delta processing enqueue a delete path for `is_deleted` items, or is this only handled by the webhook `item:deleted` event?
   - Recommendation: For safety, the reconciler SHOULD handle `is_deleted` items from the delta — the webhook may have been missed. Enqueue `sync_task` (which will detect missing Todoist task on fetch and handle accordingly), or handle directly in the sweep.

---

## Environment Availability

Step 2.6: SKIPPED — Phase 7 has no new external dependencies. All required services (Postgres, Redis, Zoho API, Todoist API, Resend) were available and verified in earlier phases.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio 0.28.0 |
| Config file | pyproject.toml (`asyncio_mode = "auto"`) |
| Quick run command | `python -m pytest tests/unit/test_reconciler.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SEED-5 | reconcile_sweep enqueues sync_task for Zoho tasks with hash mismatch | unit | `pytest tests/unit/test_reconciler.py::test_reconcile_zoho_mismatch -x` | ❌ Wave 0 |
| SEED-5 | reconcile_sweep enqueues sync_task for Todoist delta items | unit | `pytest tests/unit/test_reconciler.py::test_reconcile_todoist_delta -x` | ❌ Wave 0 |
| SEED-5 | sync_token is saved after successful delta poll | unit | `pytest tests/unit/test_reconciler.py::test_sync_token_saved -x` | ❌ Wave 0 |
| SEED-5 | reconciler_last_run kv key is updated after sweep | unit | `pytest tests/unit/test_reconciler.py::test_reconciler_last_run_updated -x` | ❌ Wave 0 |
| SEED-6 | orphan_sweep increments orphan_check_count on first 404 | unit | `pytest tests/unit/test_reconciler.py::test_orphan_first_cycle -x` | ❌ Wave 0 |
| SEED-6 | orphan_sweep triggers deletion on second 404 (EDGE-5) | unit | `pytest tests/unit/test_reconciler.py::test_orphan_second_cycle_deletion -x` | ❌ Wave 0 |
| SEED-6 | orphan_check_count reset to 0 after healthy verification | unit | `pytest tests/unit/test_reconciler.py::test_orphan_count_reset -x` | ❌ Wave 0 |
| EDGE-1 | Zoho reassignment detected via Owner.id check (not 404) | unit | `pytest tests/unit/test_reconciler.py::test_orphan_reassignment_detected -x` | ❌ Wave 0 |
| EDGE-2 | Todoist 404 triggers zoho deletion + notification | unit | `pytest tests/unit/test_reconciler.py::test_orphan_todoist_missing -x` | ❌ Wave 0 |
| EDGE-8 | Missing footer detected and re-attached via update_task | unit | `pytest tests/unit/test_reconciler.py::test_refooter_missing_footer -x` | ❌ Wave 0 |
| SYNC-10 | Duplicate enqueue_sync call is deduplicated (job returns None) | unit | `pytest tests/unit/test_reconciler.py::test_reconcile_dedup -x` | ❌ Wave 0 |
| WorkerSettings | cron_jobs registered correctly (2 entries, correct minutes) | unit | `pytest tests/unit/test_worker_settings.py::test_cron_jobs_registered -x` | ❌ Wave 0 |

### Wave 0 Gaps

- [ ] `tests/unit/test_reconciler.py` — all reconciler unit tests (≥12 test cases above)
- [ ] Add `test_cron_jobs_registered` to `tests/unit/test_worker_settings.py`

*(No framework install needed — pytest-asyncio already configured)*

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — |
| V3 Session Management | no | — |
| V4 Access Control | no | — |
| V5 Input Validation | yes (limited) | Zoho task records are fetched from authenticated API; validate `Owner.id` is a string before comparison |
| V6 Cryptography | no | — |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Orphan sweep deleting tasks based on transient 404 | Tampering / DoS | Two-cycle confirmation (EDGE-5); rate-limit errors skip rather than count as 404 |
| Notification email flooding from repeated orphan sweeps | DoS | Orphan row deleted after handling — no re-notification on next sweep |

---

## Sources

### Primary (HIGH confidence)
- arq 0.28.0 installed source — `cron()` signature, `WorkerSettings` pickup via `get_kwargs`, `cron_jobs` parameter on `Worker.__init__` [VERIFIED: `python3 -c "import arq; inspect.getsource(arq.cron.cron)"`]
- `app/db/models.py` — `sync_state.orphan_check_count` column confirmed [VERIFIED: codebase read]
- `app/todoist/sync_manager.py` — `load_sync_token`, `save_sync_token` confirmed [VERIFIED: codebase read]
- `app/zoho/client.py` — `fetch_tasks_modified_since`, `get_task` confirmed [VERIFIED: codebase read]
- `app/todoist/client.py` — `fetch_sync_delta`, `fetch_todoist_task` confirmed; `get_task` NOT defined [VERIFIED: codebase read]
- `app/worker/enqueue.py` — `enqueue_sync` with dedup confirmed [VERIFIED: codebase read]
- `app/core/notifications.py` — `send_deletion_notification` confirmed [VERIFIED: codebase read]
- `app/worker/settings.py` — `WorkerSettings` class structure; `cron_jobs` not yet present [VERIFIED: codebase read]

### Secondary (MEDIUM confidence)
- REQUIREMENTS.md / ROADMAP.md — phase requirements and success criteria [VERIFIED: codebase read]

---

## Metadata

**Confidence breakdown:**
- arq cron API: HIGH — verified via installed source in this session
- Reusable primitives: HIGH — all modules read and confirmed
- Orphan sweep design: HIGH — direct from requirements; schema already supports it
- EDGE-8 re-footer update_task signature: LOW — A1 assumption; verify in Wave 0

**Research date:** 2026-04-24
**Valid until:** 2026-05-24 (arq 0.28.0 pinned; no version drift risk)
