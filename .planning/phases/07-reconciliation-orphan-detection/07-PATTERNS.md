# Phase 7: Reconciliation & Orphan Detection - Pattern Map

**Mapped:** 2026-04-24
**Files analyzed:** 3 new/modified files
**Analogs found:** 3 / 3

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `app/worker/reconciler.py` | service / worker-cron | event-driven (scheduled), CRUD reads + enqueue | `app/todoist/sync_manager.py` (startup_sync pattern) + `app/worker/jobs.py` (ctx pattern, DB access, error handling) | role-match composite |
| `app/worker/settings.py` | config | — | `app/worker/settings.py` itself (add `cron_jobs` attribute) | exact (modification) |
| `tests/unit/test_reconciler.py` | test | — | `tests/unit/test_worker_jobs.py` + `tests/unit/test_todoist_sync_manager.py` | exact (same patterns) |

---

## Pattern Assignments

### `app/worker/reconciler.py` (service/cron, event-driven + batch)

**Analogs:** `app/todoist/sync_manager.py` (sync-token + delta loop pattern) and `app/worker/jobs.py` (ctx dict, session_factory, error handling, DB writes)

---

#### Imports pattern

Copy from `app/worker/jobs.py` lines 1-56 (import style, `from __future__ import annotations`, lazy imports, typed exception imports) combined with `app/todoist/sync_manager.py` lines 1-24 (sync_manager imports):

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.hash import canonical_hash
from app.core.logging import get_logger
from app.core.notifications import send_deletion_notification
from app.db.models import SyncEvent, SyncState
from app.todoist.client import TodoistAPIError, TodoistNotFoundError, TodoistRateLimitError
from app.todoist.normalise import extract_zoho_id
from app.todoist.sync_manager import load_sync_token, save_sync_token
from app.todoist.writer import delete_todoist_task
from app.worker.enqueue import enqueue_sync
from app.zoho.client import ZohoAPIError, ZohoNotFoundError, ZohoRateLimitError
from app.zoho.normalise import zoho_record_to_normalised
from app.zoho.state import token_state
from app.zoho.token_manager import upsert_kv
from app.zoho.writer import delete_zoho_task
```

---

#### ctx dict access pattern

Copy from `app/worker/jobs.py` lines 73-78. Every arq function (both regular jobs and cron) receives `ctx` as its first argument. Access clients and factory the same way:

```python
# app/worker/jobs.py lines 73-78
async def sync_task(ctx: dict, zoho_task_id: str) -> None:
    redis = ctx["redis"]
    session_factory = ctx["session_factory"]
    zoho_client = ctx["zoho_client"]
    todoist_client = ctx["todoist_client"]
    job_try: int = ctx["job_try"]
```

For cron functions `job_try` is absent; all other keys are the same. Cron functions have signature `async def reconcile_sweep(ctx: dict) -> None`.

---

#### session_factory async context manager pattern

Copy from `app/worker/jobs.py` lines 122-126 (read-only) and lines 140-148 (transactional with begin):

```python
# Read-only session (no commit needed)
async with session_factory() as session:
    result = await session.execute(
        select(SyncState).where(SyncState.zoho_task_id == zoho_task_id)
    )
    state = result.scalar_one_or_none()

# Transactional session (for writes / row updates)
async with session_factory() as session:
    async with session.begin():
        locked = await session.get(SyncState, state.zoho_task_id)
        locked.orphan_check_count += 1
```

The `session.begin()` context manager auto-commits on exit and auto-rolls back on exception. This is the project-standard pattern — do NOT call `session.commit()` manually inside `session.begin()`.

---

#### SELECT all rows (full table scan for orphan sweep)

No direct existing analog for a table scan, but the single-row pattern from `app/worker/jobs.py` line 146 generalises:

```python
# Orphan sweep: load all sync_state rows (no WHERE clause)
async with session_factory() as session:
    result = await session.execute(select(SyncState))
    rows = result.scalars().all()
```

---

#### upsert_kv for last_run timestamp

Copy from `app/zoho/token_manager.py` lines 65-73 (`upsert_kv` definition) and the call pattern from `app/worker/settings.py` lines 71-74:

```python
# app/worker/settings.py lines 71-74
async with session_factory() as session:
    await upsert_kv(session, KV_ACCESS_TOKEN_KEY, access_token)
    await upsert_kv(session, KV_EXPIRES_AT_KEY, expires_at.isoformat())
    await session.commit()
```

For reconciler last_run writes: `upsert_kv` does NOT auto-commit (Phase 2 contract). Caller MUST commit:

```python
async with session_factory() as session:
    await upsert_kv(session, KV_RECONCILER_LAST_RUN, datetime.now(timezone.utc).isoformat())
    await session.commit()
```

---

#### sync_token load/save pattern

Copy from `app/todoist/sync_manager.py` lines 63-74 (`startup_sync` flow). The reconciler replicates this exact two-session split (load in one session, save in another after the API call):

```python
# app/todoist/sync_manager.py lines 63-74
async with session_factory() as session:
    stored_token = await load_sync_token(session)

items, new_token = await todoist_client.fetch_sync_delta(
    sync_token=stored_token,
    project_id=settings.todoist_project_id,
)

# SEED-7: persist BEFORE processing (crash-safe)
async with session_factory() as session:
    await save_sync_token(session, new_token)
```

---

#### Todoist delta item loop pattern

Copy from `app/todoist/sync_manager.py` lines 81-95. For the reconciler, items with `is_deleted=True` should be enqueued (not just skipped) so the sync pipeline can handle the deletion. Items without `[zoho:ID]` footer are still discarded:

```python
# app/todoist/sync_manager.py lines 81-95 — base pattern
for item in items:
    if item.get("is_deleted"):
        deleted += 1
        log.info("todoist_item_deleted_skipped", todoist_id=item.get("id"))
        continue
    zoho_id = extract_zoho_id(item.get("description"))
    if zoho_id is None:
        discarded_no_footer += 1
        log.info("todoist_item_no_footer_discarded", todoist_id=item.get("id"))
        continue
    processed += 1
    # Reconciler: enqueue_sync(redis, zoho_id, defer_secs=0) here
```

---

#### enqueue_sync call pattern

Copy from `app/worker/enqueue.py` lines 21-48. The reconciler is a Todoist-triggered path (defer=0), NOT a Zoho-webhook-triggered path (defer=zoho_job_defer_secs):

```python
# app/worker/enqueue.py lines 31-36
job = await redis.enqueue_job(
    "sync_task",
    zoho_task_id,
    _job_id=f"sync:{zoho_task_id}",
    _defer_by=defer_secs,
)
# Caller uses the helper:
await enqueue_sync(redis, zoho_task_id, defer_secs=0)
```

---

#### Error handling: skip-on-rate-limit pattern

Copy from `app/worker/jobs.py` lines 90-99 (retry on API errors). The orphan sweep diverges: instead of raising `Retry`, it logs WARN and `continue`s to the next row (the next hourly sweep will retry):

```python
# jobs.py lines 90-99 — the project raises Retry for rate limits:
except (ZohoRateLimitError, ZohoAPIError, TodoistRateLimitError, TodoistAPIError) as exc:
    delay = RETRY_DELAYS.get(job_try, 60)
    log.error("sync_task_api_error_will_retry", ...)
    raise Retry(defer=delay) from exc

# Orphan sweep variant — skip and continue instead of retry:
except (ZohoRateLimitError, ZohoAPIError):
    log.warning("orphan_sweep_zoho_api_error", zoho_task_id=state.zoho_task_id)
    continue  # skip this row; retry next hour
```

---

#### SyncEvent logging pattern

Copy from `app/worker/jobs.py` lines 151-158 and 184-189. Every state-changing operation logs a `SyncEvent` row inside the same `session.begin()` transaction:

```python
# app/worker/jobs.py lines 184-189
session.add(SyncEvent(
    zoho_task_id=zoho_task_id,
    action=action,
    source="worker",
    detail={"direction": direction, "new_hash": new_hash[:8]},
))
log.info("sync_task_written", zoho_task_id=zoho_task_id, action=action, direction=direction)
```

For the reconciler orphan action:

```python
session.add(SyncEvent(
    zoho_task_id=state.zoho_task_id,
    action="orphan",
    source="reconciler",
    detail={"todoist_task_id": state.todoist_task_id,
            "zoho_missing": zoho_missing,
            "todoist_missing": todoist_missing},
))
```

---

#### delete_todoist_task call pattern

Copy from `app/todoist/writer.py` lines 99-113. The function takes `(task_id: str, todoist_api: TodoistAPIAsync)`. The `TodoistAPIAsync` instance is at `todoist_client._api`:

```python
# app/worker/jobs.py line 208
await complete_todoist_task(state.todoist_task_id, todoist_client._api)

# Reconciler orphan deletion:
await delete_todoist_task(state.todoist_task_id, ctx["todoist_client"]._api)
```

---

#### delete_zoho_task call pattern

Copy from `app/zoho/writer.py` lines 95-111. Takes `(zoho_task_id: str, access_token: str)`. Access token comes from `token_state`:

```python
# app/worker/jobs.py line 212-214
access_token = token_state["access_token"]
if target_norm.is_completed:
    await complete_zoho_task(zoho_task_id, access_token)

# Reconciler orphan deletion:
access_token = token_state["access_token"]
await delete_zoho_task(state.zoho_task_id, access_token)
```

---

#### Zoho Owner check for reassignment (EDGE-1)

`zoho_client.get_task()` returns `{"data": [{...record...}]}` per `app/zoho/client.py` lines 54-65. The normalise module (`app/zoho/normalise.py` line 7-25) reads `record.get("Subject")` etc. from the inner dict. For the Owner check, access `data[0]` from the response:

```python
# Based on zoho/client.py get_task return value shape (verified in Phase 2):
record = await zoho_client.get_task(state.zoho_task_id)
data = (record.get("data") or [{}])[0]
owner_id = str((data.get("Owner") or {}).get("id", ""))
if owner_id != settings.zoho_user_id:
    zoho_missing = True  # treat reassignment same as 404
```

---

#### `zoho_record_to_normalised` call pattern

Copy from `app/worker/jobs.py` lines 118-119. Note that `zoho_record_to_normalised` takes the **inner record dict** (not the full response), and requires `terminal_statuses`:

```python
# app/worker/jobs.py lines 118-119
zoho_record = await zoho_client.get_task(zoho_task_id)
zoho_norm = zoho_record_to_normalised(zoho_record)
```

**IMPORTANT:** `app/zoho/normalise.py` line 7 shows the actual signature is `zoho_record_to_normalised(record: dict, terminal_statuses: list[str])`. In `jobs.py` line 119, `terminal_statuses` is passed. Check the jobs.py call site — it may use `get_settings().zoho_terminal_statuses_list`. For the reconciler's hash mismatch check: pass the inner dict (`record["data"][0]`) and `get_settings().zoho_terminal_statuses_list`.

---

### `app/worker/settings.py` (config, modification)

**Analog:** `app/worker/settings.py` itself (lines 113-120 — the class body to extend)

**Current state** (`app/worker/settings.py` lines 113-120):

```python
class WorkerSettings:
    functions = [
        func(sync_task, timeout=60, keep_result=300, max_tries=4),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
```

**After modification** — add `cron_jobs` attribute and import `cron` + the two reconciler functions:

```python
# New imports to add at top of settings.py:
from arq import cron, func
from app.worker.reconciler import orphan_sweep, reconcile_sweep

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

`cron` is already exported from the `arq` top-level package (same as `func`). `cron_jobs` is picked up by `arq.worker.get_kwargs` which reads `WorkerSettings.__dict__` and passes matching `Worker.__init__` parameter names through. [VERIFIED per RESEARCH.md]

---

### `tests/unit/test_reconciler.py` (test)

**Analogs:** `tests/unit/test_worker_jobs.py` (primary) and `tests/unit/test_todoist_sync_manager.py` (secondary)

---

#### Test file header and imports pattern

Copy from `tests/unit/test_worker_jobs.py` lines 1-14:

```python
"""Unit tests for app.worker.reconciler — reconcile_sweep and orphan_sweep crons."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.core.hash import canonical_hash
from app.core.normalise import NormalisedTask
from app.zoho.client import ZohoAPIError, ZohoRateLimitError, ZohoNotFoundError
from app.todoist.client import TodoistAPIError, TodoistNotFoundError, TodoistRateLimitError
```

---

#### `complete_env` fixture usage pattern

Copy from `tests/unit/test_worker_jobs.py` — every test function takes `complete_env` as a parameter (provided by `tests/conftest.py`). The `complete_env` fixture sets all required env vars via `monkeypatch.setenv`.

```python
# tests/conftest.py lines 18-23
@pytest.fixture
def complete_env(monkeypatch):
    """Populate every required env var with a dummy value."""
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    return REQUIRED_ENV

# Usage in test:
@pytest.mark.asyncio
async def test_reconcile_zoho_mismatch(complete_env):
    from app.worker.reconciler import reconcile_sweep
    ...
```

---

#### `_make_ctx` helper pattern

Copy from `tests/unit/test_worker_jobs.py` lines 21-31. Cron functions don't use `job_try`, but all other ctx keys are the same:

```python
# test_worker_jobs.py lines 21-31
def _make_ctx(lock_acquired=True, job_try=1):
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True if lock_acquired else None)
    mock_redis.delete = AsyncMock()
    return {
        "redis": mock_redis,
        "session_factory": MagicMock(),
        "zoho_client": AsyncMock(),
        "todoist_client": AsyncMock(),
        "job_try": job_try,
    }

# Reconciler variant (no job_try, no lock needed):
def _make_reconciler_ctx():
    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock(return_value=MagicMock())
    return {
        "redis": mock_redis,
        "session_factory": MagicMock(),
        "zoho_client": AsyncMock(),
        "todoist_client": AsyncMock(),
    }
```

---

#### `_mock_session_factory_with_state` pattern

Copy from `tests/unit/test_worker_jobs.py` lines 34-52. This is the canonical mock for the `async with session_factory() as session:` pattern. For the orphan sweep's table scan (returning multiple rows), adapt `scalars().all()`:

```python
# test_worker_jobs.py lines 34-52
def _mock_session_factory_with_state(state):
    sess = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=state)
    result.scalar_one = MagicMock(return_value=state)
    sess.execute = AsyncMock(return_value=result)
    sess.add = MagicMock()
    sess.commit = AsyncMock()
    sess.begin = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    ))
    factory_ctx = AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    )
    factory = MagicMock(return_value=factory_ctx)
    return factory, sess

# For orphan sweep with multiple rows:
def _mock_session_factory_with_rows(rows):
    sess = AsyncMock()
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    sess.execute = AsyncMock(return_value=result)
    sess.get = AsyncMock(return_value=rows[0] if rows else None)
    sess.add = MagicMock()
    sess.commit = AsyncMock()
    sess.begin = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    ))
    factory_ctx = AsyncMock(
        __aenter__=AsyncMock(return_value=sess),
        __aexit__=AsyncMock(return_value=None),
    )
    factory = MagicMock(return_value=factory_ctx)
    return factory, sess
```

---

#### monkeypatch for module-level functions pattern

Copy from `tests/unit/test_todoist_sync_manager.py` lines 47-58. For the reconciler, use `monkeypatch.setattr` on module-level functions (`upsert_kv`, `enqueue_sync`, `load_sync_token`, `save_sync_token`):

```python
# test_todoist_sync_manager.py lines 51-58
async def fake_upsert(s, k, v):
    upserts.append((k, v))
monkeypatch.setattr("app.todoist.sync_manager.upsert_kv", fake_upsert)

# Reconciler equivalent:
monkeypatch.setattr("app.worker.reconciler.upsert_kv", AsyncMock())
monkeypatch.setattr("app.worker.reconciler.enqueue_sync", AsyncMock())
monkeypatch.setattr("app.worker.reconciler.load_sync_token", AsyncMock(return_value="*"))
monkeypatch.setattr("app.worker.reconciler.save_sync_token", AsyncMock())
```

---

#### SyncState mock for orphan tests pattern

Orphan tests need `SyncState`-like objects with specific attributes. Use `MagicMock` with explicit attribute assignment, mirroring `test_worker_jobs.py` lines 106-111:

```python
state = MagicMock()
state.zoho_task_id = "Z1"
state.todoist_task_id = "T1"
state.last_hash = "abc"
state.orphan_check_count = 0  # first cycle
```

---

#### `test_cron_jobs_registered` in `test_worker_settings.py`

Add to `tests/unit/test_worker_settings.py`, following the `importlib.reload` pattern from lines 27-36 of that file:

```python
def test_cron_jobs_registered(complete_env):
    """WorkerSettings.cron_jobs has 2 entries with correct minute schedules."""
    import importlib
    import app.worker.settings as ws_mod
    importlib.reload(ws_mod)
    cron_jobs = ws_mod.WorkerSettings.cron_jobs
    assert len(cron_jobs) == 2
    # reconcile_sweep fires at 0,15,30,45 minutes
    # orphan_sweep fires at minute=0
    names = {cj.coroutine.__name__ for cj in cron_jobs}
    assert "reconcile_sweep" in names
    assert "orphan_sweep" in names
```

---

## Shared Patterns

### structlog logger instantiation
**Source:** `app/worker/jobs.py` line 57; `app/todoist/sync_manager.py` line 24
**Apply to:** `app/worker/reconciler.py`

```python
from app.core.logging import get_logger
log = get_logger(__name__)
```

### settings access (lazy import to avoid module-level Settings call)
**Source:** `app/todoist/writer.py` line 47; `app/zoho/writer.py` line 81
**Apply to:** `app/worker/reconciler.py`

```python
# At top of function body, not module level:
from app.core.config import get_settings
settings = get_settings()
```

### token_state access for access_token
**Source:** `app/worker/jobs.py` lines 212-214; `app/zoho/writer.py` (imported from `app.zoho.state`)
**Apply to:** `app/worker/reconciler.py` (for `delete_zoho_task` calls)

```python
from app.zoho.state import token_state
# Inside async function:
access_token = token_state["access_token"]
```

### `send_deletion_notification` (fire-and-forget, EDGE-6)
**Source:** `app/core/notifications.py` lines 16-27; called in `app/todoist/writer.py` lines 109-113 and `app/zoho/writer.py` lines 107-111
**Apply to:** `app/worker/reconciler.py` `_handle_orphan` function

The function never re-raises. Wrap the call in `try/except` only if you need to log additional context; otherwise call it bare (it already swallows internally):

```python
# app/core/notifications.py lines 16-27
async def send_deletion_notification(subject: str, html: str) -> None:
    """Fire-and-forget Resend email. EDGE-6: failure logged, NOT re-raised."""
    try:
        ...
        await resend.Emails.send_async(params)
    except Exception as exc:
        log.error("resend_email_failed", error=str(exc))
        # Do NOT re-raise — EDGE-6.
```

---

## No Analog Found

None — all new functionality orchestrates existing primitives. The reconciler module has no exact analog (no other cron-style orchestrator exists yet), but its sub-patterns are all drawn from existing code.

---

## Metadata

**Analog search scope:** `app/worker/`, `app/todoist/`, `app/zoho/`, `app/core/`, `app/db/`, `tests/unit/`
**Files scanned:** 14 source files + 2 test files read in full
**Pattern extraction date:** 2026-04-24
