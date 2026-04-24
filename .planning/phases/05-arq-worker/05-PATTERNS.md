# Phase 5: arq Worker - Pattern Map

**Mapped:** 2026-04-24
**Files analyzed:** 6 new files
**Analogs found:** 6 / 6

---

## File Classification

| New File | Role | Data Flow | Closest Analog | Match Quality |
|----------|------|-----------|----------------|---------------|
| `app/worker/__init__.py` | package-marker | — | `app/todoist/__init__.py` | exact |
| `app/worker/__main__.py` | entry-point | request-response | `app/main.py` (lifespan wiring) | role-match |
| `app/worker/settings.py` | config | request-response | `app/main.py` (lifespan startup/shutdown) | role-match |
| `app/worker/jobs.py` | service | event-driven | `app/todoist/sync_manager.py` + writers | role-match |
| `tests/unit/test_worker_jobs.py` | test | event-driven | `tests/unit/test_todoist_writer.py` | exact |
| `tests/unit/test_worker_settings.py` | test | request-response | `tests/unit/test_token_manager.py` | exact |

---

## Pattern Assignments

### `app/worker/__init__.py` (package-marker)

**Analog:** `app/todoist/__init__.py` (empty file)

Empty file — no content needed. Creates the Python package.

---

### `app/worker/__main__.py` (entry-point)

**Analog:** `app/main.py` — the only other process entry point in the project.

**Pattern:** The entry point is minimal — it delegates entirely to the framework runner.

```python
# app/main.py — top-level entry point structure (lines 1-5, 124-125)
from fastapi import FastAPI
# ...imports...
app = FastAPI(lifespan=lifespan)
```

For the worker, the equivalent is:

```python
# app/worker/__main__.py — copy this exact structure
from arq import run_worker
from app.worker.settings import WorkerSettings

if __name__ == "__main__":
    run_worker(WorkerSettings)
```

**Railway command:** `python -m app.worker`

---

### `app/worker/settings.py` (config, startup/shutdown lifecycle)

**Analog:** `app/main.py` — the `lifespan` async context manager is the direct analog for `on_startup`/`on_shutdown` hooks.

**Imports pattern** (`app/main.py` lines 1-24):
```python
import asyncio
import resend
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.todoist.client import TodoistClient
from app.zoho.client import ZohoClient
from app.zoho.state import token_state
from app.zoho.token_manager import (
    KV_ACCESS_TOKEN_KEY, KV_EXPIRES_AT_KEY,
    load_token_from_kv, proactive_refresh_loop, refresh_access_token, upsert_kv,
)
```

**Token load + refresh pattern** (`app/main.py` lines 46-66) — copy verbatim into `on_startup`:
```python
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

token_state["access_token"] = access_token
```

**Engine + session_factory construction pattern** (`app/main.py` lines 42-45):
```python
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)
```

**Client construction pattern** (`app/main.py` lines 70-71, 102-103):
```python
client = ZohoClient(access_token=access_token)
todoist_client = TodoistClient(api_token=settings.todoist_api_token)
```

**Shutdown cleanup pattern** (`app/main.py` lines 113-120):
```python
refresh_task.cancel()
try:
    await refresh_task
except (asyncio.CancelledError, Exception):
    pass
await app.state.todoist_client.close()
await engine.dispose()
```

**WorkerSettings class structure** (arq convention — no existing project analog; use RESEARCH.md pattern directly):
```python
from arq import func
from arq.connections import RedisSettings
from app.core.config import get_settings
from app.worker.jobs import sync_task

class WorkerSettings:
    functions = [
        func(sync_task, timeout=60, keep_result=300, max_tries=4),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
```

**Critical:** `max_tries=4` means 1 initial attempt + 3 retries. arq's default is 5 — must override explicitly.

---

### `app/worker/jobs.py` (service, event-driven)

**Primary analog:** `app/todoist/sync_manager.py` — orchestration function that reads state, processes items, and logs results. Same pattern: async function that composes multiple service calls, uses session_factory, and writes to DB.

**Secondary analog:** `app/zoho/writer.py` + `app/todoist/writer.py` — how write calls are structured (typed exceptions, logging after success).

**Imports pattern** (model from `app/todoist/sync_manager.py` lines 1-24 + `app/zoho/writer.py` lines 1-28):
```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from arq import Retry
from sqlalchemy import select

from app.core.config import get_settings
from app.core.hash import canonical_hash
from app.core.logging import get_logger
from app.db.models import SyncEvent, SyncState
from app.todoist.client import TodoistAPIError, TodoistNotFoundError, TodoistRateLimitError
from app.todoist.normalise import todoist_task_to_normalised
from app.todoist.writer import (
    complete_todoist_task, create_todoist_task, delete_todoist_task, update_todoist_task,
)
from app.zoho.client import ZohoAPIError, ZohoNotFoundError, ZohoRateLimitError
from app.zoho.normalise import zoho_record_to_normalised
from app.zoho.state import token_state, zoho_field_cache
from app.zoho.writer import (
    complete_zoho_task, delete_zoho_task, update_zoho_task, write_todoist_id_to_zoho,
)

log = get_logger(__name__)
```

**Logger construction pattern** (used in every module, e.g. `app/zoho/writer.py` line 15, `app/todoist/sync_manager.py` line 24):
```python
log = get_logger(__name__)
```

**Structlog call pattern** (from `app/zoho/writer.py` and `app/todoist/writer.py`):
```python
log.info("event_name_snake_case", key=value, other_key=other_value)
log.warning("event_name_snake_case", zoho_task_id=zoho_task_id)
log.error("event_name_snake_case", error=str(exc))
```

**Typed exception pattern** — import, not redefine (from `app/todoist/writer.py` lines 22-26):
```python
from app.todoist.client import (
    TodoistAPIError, TodoistAuthError, TodoistNotFoundError, TodoistRateLimitError,
)
from app.zoho.client import (
    ZohoAPIError, ZohoAuthError, ZohoNotFoundError, ZohoRateLimitError,
)
```

**Session factory usage pattern** (`app/todoist/sync_manager.py` lines 63-75) — always use `async with session_factory() as session:` and commit explicitly:
```python
async with session_factory() as session:
    stored_token = await load_sync_token(session)
# ...
async with session_factory() as session:
    await save_sync_token(session, new_token)
```

**SELECT FOR UPDATE pattern** (no existing analog — use RESEARCH.md Pattern 3 directly):
```python
from sqlalchemy import select
from app.db.models import SyncState

async with session_factory() as session:
    async with session.begin():
        row = await session.execute(
            select(SyncState)
            .where(SyncState.zoho_task_id == zoho_task_id)
            .with_for_update()
        )
        state = row.scalar_one_or_none()
        # hash comparison and DB write happen here (under lock)
        # session.begin() exit → commit → lock released
```

**SyncEvent insert pattern** (model from `app/db/models.py` lines 28-40):
```python
session.add(SyncEvent(
    zoho_task_id=zoho_task_id,
    action="echo_suppressed",   # one of: sync, echo_suppressed, overwrite, orphan, error
    source="worker",            # one of: zoho_webhook, todoist_webhook, reconciler, migration
    detail={"hash": new_hash[:8]},  # JSONB — keep concise
))
```

**SyncState insert pattern** (model from `app/db/models.py` lines 13-26):
```python
session.add(SyncState(
    zoho_task_id=zoho_task_id,
    todoist_task_id=todoist_id,
    last_hash=new_hash,
    last_synced_at=datetime.now(timezone.utc),
))
```

**SETNX Redis lock pattern** (no existing analog — use RESEARCH.md Pattern 4 directly):
```python
lock_key = f"lock:sync:{zoho_task_id}"
acquired = await redis.set(lock_key, "1", nx=True, ex=30)
if not acquired:
    log.warning("sync_task_lock_not_acquired", zoho_task_id=zoho_task_id)
    return
try:
    ...  # critical work
finally:
    await redis.delete(lock_key)
```

**arq retry pattern** (no existing analog — use RESEARCH.md Pattern 5 directly):
```python
RETRY_DELAYS = {1: 5, 2: 15, 3: 60}

async def sync_task(ctx: dict, zoho_task_id: str) -> None:
    job_try: int = ctx["job_try"]
    # ...
    except (ZohoRateLimitError, ZohoAPIError, TodoistRateLimitError, TodoistAPIError) as exc:
        delay = RETRY_DELAYS.get(job_try, 60)
        log.error("sync_task_api_error_will_retry", zoho_task_id=zoho_task_id, attempt=job_try, delay=delay)
        raise Retry(defer=delay) from exc
```

**enqueue_sync helper** (called by Phase 6/7 callers — no existing caller pattern; use RESEARCH.md Pattern 6):
```python
async def enqueue_sync(redis: "ArqRedis", zoho_task_id: str, defer_secs: int = 0) -> None:
    job = await redis.enqueue_job(
        "sync_task",
        zoho_task_id,
        _job_id=f"sync:{zoho_task_id}",
        _defer_by=defer_secs,
    )
    if job is None:
        log.warning("sync_task_dedup_dropped", zoho_task_id=zoho_task_id)
```

**LOOP-5 guard — footer check before reverse sync** (from `app/todoist/sync_manager.py` lines 86-92):
```python
from app.todoist.normalise import extract_zoho_id
# ...
zoho_id = extract_zoho_id(item.get("description"))
if zoho_id is None:
    log.info("todoist_item_no_footer_discarded", todoist_id=item.get("id"))
    return
```

---

### `tests/unit/test_worker_jobs.py` (test, event-driven)

**Analog:** `tests/unit/test_todoist_writer.py` — best match: async unit tests with `AsyncMock` for external clients, `complete_env` fixture for env vars, explicit assertion on call arguments.

**Test file imports pattern** (`tests/unit/test_todoist_writer.py` lines 1-15):
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.todoist.writer import create_todoist_task, ...
from app.todoist.client import TodoistAuthError, TodoistRateLimitError, ...
from app.core.normalise import NormalisedTask
```

**Async test declaration** (`tests/unit/test_todoist_writer.py` line 17) — `asyncio_mode = "auto"` in pyproject.toml means no decorator needed, but `@pytest.mark.asyncio` is still acceptable and used explicitly in `test_todoist_writer.py`:
```python
@pytest.mark.asyncio
async def test_something(complete_env):
    ...
```

**`complete_env` fixture usage** (`tests/conftest.py` lines 18-23) — always include for any test touching `get_settings()`:
```python
@pytest.fixture
def complete_env(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    return REQUIRED_ENV
```

**AsyncMock for client methods** (`tests/unit/test_todoist_writer.py` lines 21-23):
```python
mock_api = AsyncMock()
mock_task = MagicMock()
mock_task.id = "T999"
mock_api.add_task = AsyncMock(return_value=mock_task)
```

**Mock session factory pattern** — for `sync_task` unit tests, mock the session_factory as a context manager returning an AsyncMock session:
```python
from unittest.mock import AsyncMock, MagicMock, patch

mock_session = AsyncMock()
mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
mock_session_factory = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_session), __aexit__=AsyncMock()))
```

**Mock `ctx` dict for arq job** — pattern unique to this phase; `ctx` is a plain dict:
```python
mock_redis = AsyncMock()
mock_redis.set = AsyncMock(return_value=True)   # lock acquired
mock_redis.delete = AsyncMock()

ctx = {
    "redis": mock_redis,
    "session_factory": mock_session_factory,
    "zoho_client": AsyncMock(),
    "todoist_client": AsyncMock(),
    "job_try": 1,
}
```

**Structured log assertion pattern** — project uses structlog; tests verify behavior (call counts, side effects) rather than log output directly. See `test_todoist_writer.py` throughout — no log assertions, only side-effect assertions.

---

### `tests/unit/test_worker_settings.py` (test, config)

**Analog:** `tests/unit/test_token_manager.py` — tests for config/lifecycle constants and behavior.

**Constant assertion pattern** (`tests/unit/test_token_manager.py` lines 18-19):
```python
def test_REFRESH_INTERVAL_SECS_is_50_minutes():
    assert REFRESH_INTERVAL_SECS == 3000
```

**`RedisSettings.from_dsn` test** — verify the WorkerSettings class produces the right redis_settings from the env var:
```python
async def test_redis_settings_from_dsn(complete_env):
    from arq.connections import RedisSettings
    from app.worker.settings import WorkerSettings
    from app.core.config import get_settings
    get_settings.cache_clear()
    rs = WorkerSettings.redis_settings
    assert isinstance(rs, RedisSettings)
    get_settings.cache_clear()
```

---

## Shared Patterns

### Structured Logging
**Source:** `app/core/logging.py` lines 25-26; used identically in every module
**Apply to:** `app/worker/jobs.py`, `app/worker/settings.py`
```python
from app.core.logging import get_logger
log = get_logger(__name__)
```
Event names always use `snake_case` strings. Key-value pairs passed as kwargs. Never use f-strings in the log call — always pass structured fields.

### `get_settings()` with lazy import + cache_clear in tests
**Source:** `app/core/config.py` lines 31-32; test pattern from `tests/unit/test_zoho_writer.py` lines 144-145
**Apply to:** `app/worker/settings.py` on_startup, `app/worker/jobs.py`
```python
from app.core.config import get_settings
settings = get_settings()   # cached via lru_cache
```
In tests that monkeypatch env vars, always call `get_settings.cache_clear()` before and after to avoid cross-test pollution.

### Typed Exception Import (not redefinition)
**Source:** `app/todoist/writer.py` lines 22-26, `app/zoho/writer.py` lines 19-25
**Apply to:** `app/worker/jobs.py`

All typed exceptions (`ZohoRateLimitError`, `TodoistRateLimitError`, etc.) are imported from their defining modules — never redefined in the consumer. This applies to `jobs.py` catching these for Retry decisions.

### Session Factory Usage
**Source:** `app/todoist/sync_manager.py` lines 63-74
**Apply to:** `app/worker/jobs.py`, `app/worker/settings.py` on_startup
```python
async with session_factory() as session:
    # reads
async with session_factory() as session:
    async with session.begin():
        # writes that require atomicity
        session.add(...)
    # session.begin() exit commits and releases any FOR UPDATE lock
```
Each context manager call is a separate session. Never share a session between the lock-acquisition step and the post-lock processing step.

### `complete_env` Fixture
**Source:** `tests/conftest.py` lines 18-23
**Apply to:** All tests in `tests/unit/test_worker_jobs.py` and `tests/unit/test_worker_settings.py`

Every test that calls any module importing `get_settings()` at module scope must use `complete_env` as a fixture parameter. The fixture monkeypatches all required env vars.

### `from __future__ import annotations`
**Source:** `app/todoist/writer.py` line 9, `app/todoist/client.py` line 9, `app/todoist/sync_manager.py` line 9
**Apply to:** `app/worker/jobs.py`, `app/worker/settings.py`

All modules using TYPE_CHECKING or forward references use `from __future__ import annotations` as the first import.

---

## No Analog Found

All six new files have analogs. No entries in this section.

---

## Metadata

**Analog search scope:** `app/`, `tests/unit/`
**Files scanned:** 26 source files, 19 test files
**Pattern extraction date:** 2026-04-24
