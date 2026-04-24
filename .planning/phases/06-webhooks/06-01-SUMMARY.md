---
phase: 06-webhooks
plan: "01"
subsystem: web
tags: [fastapi, webhooks, zoho, arq, lifespan, tdd]
dependency_graph:
  requires:
    - 05-02 (enqueue_sync, ArqRedis)
    - 01-xx (DB models, session_factory)
    - 02-xx (ZohoClient, lifespan token bootstrap)
  provides:
    - app/webhooks package with Zoho handler and Todoist stub
    - app.state.redis (ArqRedis pool) on FastAPI app
    - app.state.session_factory on FastAPI app
    - POST /webhooks/zoho registered and live
    - POST /webhooks/todoist registered (stub for Plan 02)
  affects:
    - app/main.py (lifespan extended, router mounted)
    - tests/unit/test_main_lifespan.py (create_pool mocked)
tech_stack:
  added: []
  patterns:
    - FastAPI APIRouter mounted at /webhooks prefix
    - arq.connections.create_pool in lifespan for ArqRedis pool
    - ids list-or-string normalisation (Zoho open question A3)
    - TestClient with pre-stubbed app.state (bypasses lifespan)
key_files:
  created:
    - app/webhooks/__init__.py
    - app/webhooks/router.py
    - tests/unit/test_webhooks.py
  modified:
    - app/main.py
    - tests/unit/test_main_lifespan.py
decisions:
  - "ArqRedis pool created in lifespan step 5 (after Todoist startup_sync) so boot failure before Redis doesn't need pool cleanup"
  - "ids normalisation accepts list or bare string (open question A3); first element of list is stringified"
  - "test_main_lifespan.py _patched_lifespan fixture extended with create_pool mock — minimal change, no rewrites"
  - "Todoist stub returns 200; test assertion is status_code in (200, 401) so Plan 02 can tighten to 401 without breaking Plan 01"
metrics:
  duration: "8 minutes"
  completed_date: "2026-04-24"
  tasks_completed: 2
  files_changed: 5
---

# Phase 6 Plan 01: Webhook Scaffold and Zoho Handler Summary

**One-liner:** Zoho notification-only webhook at `POST /webhooks/zoho` with ids normalisation, enqueue_sync dispatch, and ArqRedis pool wired on `app.state.redis` via lifespan extension.

## What Was Built

### app/webhooks/__init__.py
Package marker for the `app.webhooks` module.

### app/webhooks/router.py
FastAPI `APIRouter` with two endpoints:

- `POST /zoho`: Validates payload (`module` required, `ids` required and non-empty), normalises `ids` as list or bare string (open question A3), calls `enqueue_sync(redis, zoho_task_id, defer_secs=settings.zoho_job_defer_secs)`, returns `{"ok": True}`. Returns HTTP 400 on invalid JSON or missing/empty fields.
- `POST /todoist`: Stub returning `{"ok": True}`. Plan 02 replaces this body with HMAC-SHA256 verification and event dispatch.

### app/main.py (extensions)
Three additions to the existing lifespan:
1. Import `arq.connections.create_pool, RedisSettings` and `webhooks_router`
2. Startup step 5: `create_pool(RedisSettings.from_dsn(settings.redis_url))` → `app.state.redis`; `app.state.session_factory = session_factory`
3. Shutdown: `await app.state.redis.aclose()` before `engine.dispose()`
4. `app.include_router(webhooks_router, prefix="/webhooks", tags=["webhooks"])`

### tests/unit/test_webhooks.py
10 tests covering:
- SYNC-4: enqueue on valid payload, 400 on missing module/ids/empty-ids, 400 on invalid JSON
- A3: bare string ids and integer ids element accepted
- INFRA-4: both `/webhooks/zoho` and `/webhooks/todoist` routes registered
- INFRA-1: lifespan creates ArqRedis pool on `app.state.redis`, stores `session_factory` on `app.state.session_factory`, closes pool at shutdown
- Todoist stub: permissive assertion (`status_code in (200, 401)`)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Existing test_main_lifespan.py tests attempted real Redis connection**
- **Found during:** Task 2 GREEN phase (full suite run)
- **Issue:** Adding `create_pool` to lifespan caused `_patched_lifespan` fixture and `test_lifespan_initialises_todoist_client_and_runs_startup_sync` to attempt real Redis connections (OSError connecting to localhost:6379)
- **Fix:** Added `monkeypatch.setattr("app.main.create_pool", AsyncMock(return_value=fake_redis))` to `_patched_lifespan` fixture and `test_lifespan_initialises_todoist_client_and_runs_startup_sync`. Minimal change — no rewrites. Plan explicitly anticipated this.
- **Files modified:** `tests/unit/test_main_lifespan.py`
- **Commit:** 2331abb

**2. [Rule 3 - Blocking] Module-level get_settings import caused collection failure**
- **Found during:** Task 1 RED phase collection
- **Issue:** `from app.core.config import get_settings` at module level triggers `config.py` module-level `settings = get_settings()` which fails without env vars set
- **Fix:** Moved `get_settings` import inside `_clear_settings_cache` fixture body (lazy import), matching pattern used in `test_worker_settings.py`
- **Files modified:** `tests/unit/test_webhooks.py`

## Known Stubs

| Stub | File | Line | Reason |
|------|------|------|--------|
| `POST /todoist` returns `{"ok": True}` | `app/webhooks/router.py` | ~57 | Plan 02 adds HMAC-SHA256 verification and event dispatch; stub intentional per plan |

## Threat Surface Scan

No new trust boundaries beyond what the plan's threat model covers. All mitigations from the threat register are implemented:
- T-06-01: `zoho_task_id` passed only to `enqueue_sync` (parameterised Redis job arg)
- T-06-03: `enqueue_sync` uses `_job_id=f"sync:{zoho_task_id}"` for arq dedup
- T-06-05: Only `zoho_task_id` and `defer_secs` logged, never full payload
- T-06-06: `await app.state.redis.aclose()` in shutdown block
- T-06-08: `if not module or ids is None` 400 guard

T-06-07 (Todoist stub accepts any POST) is accepted per plan — no production traffic before Plan 02 ships.

## Self-Check

### Files exist:
- app/webhooks/__init__.py: FOUND
- app/webhooks/router.py: FOUND
- tests/unit/test_webhooks.py: FOUND (10 tests)

### Commits exist:
- 1893879 (test RED phase): FOUND
- 2331abb (feat GREEN phase): FOUND

## Self-Check: PASSED
