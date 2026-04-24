# Phase 6: Webhooks - Pattern Map

**Mapped:** 2026-04-24
**Files analyzed:** 4 (2 new modules, 1 modification, 1 new test file)
**Analogs found:** 4 / 4

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `app/webhooks/__init__.py` | package marker | — | `app/worker/__init__.py` | exact |
| `app/webhooks/router.py` | controller | request-response | `app/worker/enqueue.py` + `app/main.py` | role-match (no existing HTTP router module; `app/main.py` is the only FastAPI host) |
| `app/main.py` (modify) | config/lifecycle | request-response | `app/main.py` (existing lifespan) | exact |
| `tests/unit/test_webhooks.py` | test | request-response | `tests/unit/test_main_lifespan.py` + `tests/unit/test_worker_jobs.py` | role-match |

---

## Pattern Assignments

### `app/webhooks/__init__.py` (package marker)

**Analog:** `app/worker/__init__.py` (empty file)

No content required. Create an empty file identical to every other `__init__.py` package marker in the project. The project uses no `__all__` declarations or re-exports in package markers.

---

### `app/webhooks/router.py` (controller, request-response)

**Analog:** `app/worker/enqueue.py` (for import style + logger) and `app/main.py` (for `app.state` access pattern). No existing `APIRouter` module exists — this is the first.

**Imports pattern** — follow `app/worker/jobs.py` lines 22-50 and `app/worker/enqueue.py` lines 1-18 for import block style:

```python
from __future__ import annotations

import base64
import hashlib
import hmac

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import SyncState
from app.todoist.normalise import extract_zoho_id
from app.worker.enqueue import enqueue_sync

log = get_logger(__name__)
router = APIRouter()
```

**Core Zoho handler pattern** — source: RESEARCH.md Pattern 2 + `app/worker/enqueue.py` lines 21-48:

```python
@router.post("/zoho")
async def zoho_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    module = payload.get("module")
    ids = payload.get("ids")
    if not module or not ids:
        raise HTTPException(status_code=400, detail="missing module or ids")
    # Open question A3: ids may be string or list — normalise both
    if isinstance(ids, list):
        if not ids:
            raise HTTPException(status_code=400, detail="ids is empty")
        zoho_task_id = str(ids[0])
    else:
        zoho_task_id = str(ids)

    settings = get_settings()
    redis = request.app.state.redis
    await enqueue_sync(redis, zoho_task_id, defer_secs=settings.zoho_job_defer_secs)
    return {"ok": True}
```

**HMAC verification pattern** — source: RESEARCH.md Pattern 1 + RESEARCH.md `verify_todoist_hmac` function. CRITICAL: `request.body()` MUST be called before `request.json()`:

```python
@router.post("/todoist")
async def todoist_webhook(request: Request):
    raw_body = await request.body()  # raw bytes — MUST precede any json() call

    settings = get_settings()
    expected = base64.b64encode(
        hmac.new(
            settings.todoist_client_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    received = request.headers.get("X-Todoist-Hmac-SHA256", "")
    if not hmac.compare_digest(expected, received):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()  # safe: body already cached by Starlette
    ...
    return {"ok": True}
```

**Todoist event dispatch pattern** — source: RESEARCH.md Pattern 4 + `app/worker/jobs.py` dispatch style:

```python
    event_name = payload.get("event_name", "")
    event_data = payload.get("event_data", {})
    todoist_task_id = str(event_data.get("id", ""))

    # Project isolation gate (open question 2 — add as first filter)
    if event_data.get("project_id") != settings.todoist_project_id:
        log.debug("todoist_event_wrong_project", event_name=event_name,
                  todoist_task_id=todoist_task_id)
        return {"ok": True}

    redis = request.app.state.redis
    session_factory = request.app.state.session_factory

    if event_name == "item:added":
        description = event_data.get("description") or ""
        zoho_id = extract_zoho_id(description)
        if zoho_id is None:
            log.info("todoist_item_added_no_footer_discarded",
                     todoist_task_id=todoist_task_id)
            return {"ok": True}
        await enqueue_sync(redis, zoho_id, defer_secs=0)

    elif event_name in ("item:updated", "item:completed", "item:uncompleted"):
        zoho_id = await _lookup_zoho_id(session_factory, todoist_task_id)
        if zoho_id is None:
            log.warning("todoist_event_no_sync_state", event_name=event_name,
                        todoist_task_id=todoist_task_id)
            return {"ok": True}
        await enqueue_sync(redis, zoho_id, defer_secs=0)

    elif event_name == "item:deleted":
        zoho_id = await _lookup_zoho_id(session_factory, todoist_task_id)
        if zoho_id:
            await enqueue_sync(redis, zoho_id, defer_secs=0)

    else:
        log.debug("todoist_event_ignored", event_name=event_name)

    return {"ok": True}
```

**sync_state lookup helper** — source: RESEARCH.md Pattern 5 + `app/db/models.py` lines 13-25 (`SyncState.todoist_task_id` column + `idx_sync_state_todoist_task_id` index). Use `select(SyncState.zoho_task_id)` — fetches only the needed column:

```python
async def _lookup_zoho_id(session_factory, todoist_task_id: str) -> str | None:
    async with session_factory() as session:
        result = await session.execute(
            select(SyncState.zoho_task_id).where(
                SyncState.todoist_task_id == todoist_task_id
            )
        )
        return result.scalar_one_or_none()
```

**Error handling pattern** — `HTTPException` for protocol-level rejections (400 bad payload, 401 HMAC mismatch). All other errors surface as 500 via FastAPI default handler. Do NOT catch generic exceptions inside the Todoist handler body — let unexpected errors propagate so Railway restart occurs. Source: FastAPI default; consistent with how no try/except wraps `enqueue_sync` in `app/worker/enqueue.py`.

**Logging pattern** — copy exactly from `app/worker/enqueue.py` lines 13-18:

```python
log = get_logger(__name__)
```

Use structured key=value calls (`log.info("event_name", key=value)`), same as every other module in the project.

---

### `app/main.py` (modify — extend lifespan)

**Analog:** `app/main.py` itself (lines 30-124). The modification adds three things:

1. **New imports** at the top of `app/main.py` (after existing imports, following the same grouping — stdlib → third-party → local):

```python
from arq.connections import create_pool, RedisSettings

from app.webhooks.router import router as webhooks_router
```

2. **ArqRedis pool creation** inside the lifespan `@asynccontextmanager`, placed after the `TodoistClient` startup (step 4, line ~108) and before `yield`. Follow the exact `app.state` assignment style already used on lines 97 and 108:

```python
    # 5. Create ArqRedis pool for webhook job enqueue (Phase 6).
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.redis = redis
    # Store session_factory in app.state for Todoist webhook handler (Phase 6).
    app.state.session_factory = session_factory

    yield

    # Shutdown additions (insert before engine.dispose()):
    await app.state.redis.aclose()
```

3. **Router mount** after `app = FastAPI(lifespan=lifespan)` at the module bottom (line 124):

```python
app.include_router(webhooks_router, prefix="/webhooks", tags=["webhooks"])
```

Source: RESEARCH.md "Router Mount in main.py" + existing `app.state.zoho_refresh_task` (line 97) and `app.state.todoist_client` (line 108) patterns.

---

### `tests/unit/test_webhooks.py` (test, request-response)

**Analog:** `tests/unit/test_main_lifespan.py` (lifespan + `app.state` mocking style) and `tests/unit/test_worker_jobs.py` (async test structure, `AsyncMock`, `complete_env` fixture, `patch` usage, `pytest.mark.asyncio`).

**Test file imports pattern** — from `tests/unit/test_worker_jobs.py` lines 1-14 and `tests/unit/test_main_lifespan.py` lines 1-11:

```python
import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as main_app
```

**Test class / fixture pattern** — project uses flat functions with fixtures, not classes. `complete_env` is the standard env fixture from `tests/conftest.py`. Tests import modules lazily inside the test body (see `test_main_lifespan.py` line 88: `from app.main import lifespan`).

**TestClient usage pattern for HTTP endpoint tests** — use `httpx.AsyncClient` (project is async throughout) or `fastapi.testclient.TestClient` (sync wrapper). Prefer `TestClient` for unit tests to avoid `pytest-asyncio` event loop complications when testing HTTP handlers. Patch `app.state` attributes directly on the app object before the request.

**Recommended test fixture for webhook unit tests** — follow `test_main_lifespan.py` lines 23-84 (`_patched_lifespan` fixture):

```python
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def webhook_client(complete_env):
    """TestClient with app.state.redis and app.state.session_factory stubbed."""
    from app.main import app
    app.state.redis = AsyncMock()
    app.state.session_factory = MagicMock()
    return TestClient(app, raise_server_exceptions=True)
```

**HMAC helper for test payloads** — generate valid and invalid HMAC signatures in tests using stdlib, matching the implementation:

```python
def _make_hmac(body: bytes, secret: str = "test-todoist-client-secret") -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()
```

**Test naming pattern** — from `tests/unit/test_worker_jobs.py` docstrings and RESEARCH.md test map. Each test function name directly encodes the req ID and behavior:

```
test_zoho_webhook_enqueues                       # SYNC-4
test_zoho_missing_module_returns_400             # SYNC-4 validation
test_todoist_invalid_hmac_returns_401            # INFRA-1
test_todoist_item_added_no_footer_discarded      # SYNC-8
test_todoist_item_added_with_footer_enqueues     # LOOP-5
test_todoist_item_completed_enqueues             # EDGE-7
test_todoist_missing_footer_on_synced_task       # EDGE-8
test_router_paths                                # INFRA-4
```

**AsyncMock for `enqueue_sync`** — patch at `app.webhooks.router.enqueue_sync` (not the source module), consistent with how `test_worker_jobs.py` patches at `app.worker.jobs.*`:

```python
with patch("app.webhooks.router.enqueue_sync", new_callable=AsyncMock) as mock_enqueue:
    response = client.post("/webhooks/zoho", json={"module": "Tasks", "ids": ["999"]})
    assert response.status_code == 200
    mock_enqueue.assert_awaited_once_with(
        app.state.redis, "999", defer_secs=2
    )
```

---

## Shared Patterns

### Logger instantiation
**Source:** `app/worker/enqueue.py` lines 13-18, `app/worker/jobs.py` line 31
**Apply to:** `app/webhooks/router.py`

```python
from app.core.logging import get_logger
log = get_logger(__name__)
```

All structured log calls use keyword arguments: `log.info("event_label", key=value, ...)`. Never use f-strings in log calls.

### Settings access
**Source:** `app/worker/enqueue.py` (TYPE_CHECKING guard), `app/core/config.py` lines 30-38
**Apply to:** `app/webhooks/router.py`

```python
from app.core.config import get_settings
# ...
settings = get_settings()
```

Call `get_settings()` at the point of use inside the handler (not at module level), consistent with how the lifespan calls it (`app/main.py` line 32). This allows `get_settings.cache_clear()` + `monkeypatch.setenv()` to work in tests.

### `app.state` access for shared resources
**Source:** `app/main.py` lines 97, 108 (write); handlers read via `request.app.state.*`
**Apply to:** `app/webhooks/router.py` (both handlers)

```python
redis = request.app.state.redis
session_factory = request.app.state.session_factory
```

### `enqueue_sync` call signature
**Source:** `app/worker/enqueue.py` lines 21-48
**Apply to:** Both webhook handlers

```python
await enqueue_sync(redis, zoho_task_id, defer_secs=settings.zoho_job_defer_secs)  # Zoho-triggered
await enqueue_sync(redis, zoho_id, defer_secs=0)                                   # Todoist-triggered
```

`defer_secs` is always passed as keyword argument. `zoho_task_id` is always `str`.

### `complete_env` fixture for tests requiring env vars
**Source:** `tests/conftest.py` lines 18-23
**Apply to:** `tests/unit/test_webhooks.py`

```python
async def test_something(complete_env):
    ...
```

`complete_env` already includes `TODOIST_CLIENT_SECRET = "test-todoist-client-secret"` — use this value when generating test HMAC signatures.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `app/webhooks/router.py` (APIRouter itself) | controller | request-response | No existing `APIRouter` module in project; `app/main.py` is a plain `FastAPI()` app with no sub-routers. The router + handler pattern is net-new but all dependencies (`enqueue_sync`, `extract_zoho_id`, `get_settings`, `get_logger`, `app.state`) have established analogs. |

---

## Metadata

**Analog search scope:** `app/`, `tests/unit/`
**Files scanned:** 12 source files, 14 test files
**Key constraint confirmed:** `TODOIST_CLIENT_SECRET` is already in `Settings` (line 18 of `app/core/config.py`) and in `REQUIRED_ENV` in `tests/conftest.py` — no env var additions needed.
**Pattern extraction date:** 2026-04-24
