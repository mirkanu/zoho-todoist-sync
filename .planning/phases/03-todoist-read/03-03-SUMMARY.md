---
phase: 03-todoist-read
plan: "03"
subsystem: todoist-sync-manager
tags: [todoist, sync-token, kv-store, lifespan, seed-7, sync-8, sync-5, tdd]
dependency_graph:
  requires:
    - app/todoist/normalise.py (extract_zoho_id — Plan 01)
    - app/todoist/client.py (TodoistClient, fetch_sync_delta — Plan 02)
    - app/zoho/token_manager.py (upsert_kv — Phase 02)
    - app/db/models.py (KVStore)
  provides:
    - app/todoist/sync_manager.py (KV_SYNC_TOKEN_KEY, load_sync_token, save_sync_token, startup_sync)
    - app/main.py (lifespan wired with TodoistClient + startup_sync + close on shutdown)
  affects:
    - Phase 5 (recurring fetch_sync_delta scheduling reuses startup_sync primitives)
    - Phase 7 (reconciliation consuming sync_token state)
tech_stack:
  added: []
  patterns:
    - TDD RED/GREEN for both tasks
    - Module-level import reuse (upsert_kv imported, not redefined)
    - Crash-safe token persistence (save before process — SEED-7)
    - Fail-fast lifespan startup (exceptions propagate — SYNC-5)
key_files:
  created:
    - app/todoist/sync_manager.py
    - tests/unit/test_todoist_sync_manager.py
  modified:
    - app/main.py
    - tests/unit/test_main_lifespan.py
    - tests/unit/test_migration_and_app.py
decisions:
  - "save_sync_token commits BEFORE item loop — crash mid-processing triggers idempotent retry via hash check in Phase 5, not re-fetch (SEED-7)"
  - "startup_sync raises on any exception including TodoistAuthError — lifespan halts cleanly, client closed to prevent httpx leak (T-03-03-06)"
  - "upsert_kv imported from app.zoho.token_manager, not redefined — Phase 2 contract (no-commit) reused unchanged"
  - "load_sync_token returns '*' for both None row and empty value — corrupted kv row triggers full resync, not API error (T-03-03-01)"
  - "caplog cannot capture structlog output — log.info spy used in test_startup_sync_discards_items_without_footer"
  - "test_migration_and_app.py sys.modules restoration added — prevents ghost monkeypatch failures when module cache invalidated"
metrics:
  duration: "12 minutes"
  completed_date: "2026-04-24"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 3
requirements: [SEED-7, SYNC-8]
---

# Phase 03 Plan 03: Todoist Sync Manager + Lifespan Wiring Summary

**One-liner:** sync_token persisted to kv_store across restarts (SEED-7) with crash-safe save-before-process ordering; startup_sync discards footerless items (SYNC-8); TodoistClient lifecycle wired into FastAPI lifespan with fail-fast error propagation (SYNC-5).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for sync_manager | 7cd1676 | tests/unit/test_todoist_sync_manager.py |
| 1 (GREEN) | sync_manager load/save/startup_sync | da9d498 | app/todoist/sync_manager.py |
| 2 (RED) | Failing lifespan tests for Todoist wiring | aa676a2 | tests/unit/test_main_lifespan.py |
| 2 (GREEN) | Wire TodoistClient + startup_sync into lifespan | 4fb3e6d | app/main.py, tests/unit/test_migration_and_app.py |

## What Was Built

### app/todoist/sync_manager.py

Three primitives implementing SEED-7 and SYNC-8:

**`load_sync_token(session) -> str`**
- Reads `kv_store` row with key `"todoist_sync_token"`
- Returns `"*"` (full sync sentinel) when row is absent OR value is empty string
- Corrupted/empty token triggers full resync rather than API error (T-03-03-01)

**`save_sync_token(session, token) -> None`**
- Calls `upsert_kv` (imported from `app.zoho.token_manager` — no duplication)
- Commits immediately after — single-key atomic write

**`startup_sync(todoist_client, session_factory, settings) -> None`**
- Opens session, loads token, closes session
- Calls `fetch_sync_delta(sync_token=stored, project_id=settings.todoist_project_id)`
- Opens new session, calls `save_sync_token` with returned token — BEFORE item loop (SEED-7 crash-safety)
- Item loop: deleted items logged `todoist_item_deleted_skipped` and skipped; footerless items logged `todoist_item_no_footer_discarded` and discarded (SYNC-8); valid items counted for summary
- Logs `todoist_startup_sync_complete` with total/processed/discarded_no_footer/deleted counts

### app/main.py (lifespan additions)

Step 4 added after existing Zoho setup (before `yield`):
- `TodoistClient(api_token=settings.todoist_api_token)` constructed
- `await startup_sync(todoist_client, session_factory, settings)` — exceptions propagate (SYNC-5)
- `try/except` closes client on any boot failure (T-03-03-06: httpx leak prevention)
- `app.state.todoist_client = todoist_client` stored for downstream access

Shutdown (after `yield`, before `engine.dispose()`):
- `await app.state.todoist_client.close()` frees `httpx.AsyncClient`

### tests/unit/test_todoist_sync_manager.py

9 unit tests covering:
- `KV_SYNC_TOKEN_KEY` constant value
- `load_sync_token`: missing row → `"*"`, present value → returns it, empty value → `"*"`
- `save_sync_token`: upserts correct key/value, commits once
- `startup_sync`: full sync on missing token, incremental on stored token
- `startup_sync`: footer/deleted discard paths (log event spy — structlog bypasses caplog)
- `startup_sync`: token persisted BEFORE item processing (ordering enforced by call_order spy)

### tests/unit/test_main_lifespan.py additions

2 new tests:
- `test_lifespan_initialises_todoist_client_and_runs_startup_sync`: verifies construction, startup_sync call, app.state storage, and close on shutdown
- `test_lifespan_startup_sync_failure_propagates`: `TodoistAuthError` from `startup_sync` propagates out of lifespan context

Fixture extended with `FakeTodoistClient` and `fake_startup_sync` stubs so all 7 existing tests remain green.

## Verification

```
pytest tests/unit/test_todoist_sync_manager.py -x -q
9 passed in 0.63s

pytest tests/unit/test_main_lifespan.py tests/unit/test_todoist_sync_manager.py -q
18 passed in 0.93s

pytest tests/ -q
162 passed in 2.87s
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] caplog does not capture structlog output**
- **Found during:** Task 1 GREEN — `test_startup_sync_discards_items_without_footer` failed despite logs appearing in stdout
- **Issue:** structlog renders to stdout via its own pipeline, bypassing Python stdlib logging handlers that caplog hooks into
- **Fix:** Replaced `caplog`-based assertion with a `monkeypatch` spy on `sm.log.info` that appends event names to a list. Production code unchanged.
- **Files modified:** `tests/unit/test_todoist_sync_manager.py`
- **Commit:** da9d498 (included in GREEN commit)

**2. [Rule 3 - Blocking] test_migration_and_app.py sys.modules invalidation broke monkeypatch targeting**
- **Found during:** Task 2 GREEN — full suite showed 4 failures in `test_todoist_sync_manager.py` despite isolated run passing
- **Issue:** `test_main_py_importable_with_env` deletes all `app.*` from `sys.modules` and reimports them fresh. Subsequent tests' `monkeypatch.setattr("app.todoist.sync_manager.upsert_kv", ...)` targeted the OLD module object while production code ran through the NEW one — spy calls were invisible
- **Fix:** Added `saved_app_modules` snapshot before deletion and `sys.modules.update(saved_app_modules)` restoration in `finally` block. The test still validates importability but leaves module namespace consistent for subsequent tests.
- **Files modified:** `tests/unit/test_migration_and_app.py`
- **Commit:** 4fb3e6d

## Threat Mitigations Verified

| Threat ID | Status |
|-----------|--------|
| T-03-03-01: Corrupted sync_token | Mitigated — `load_sync_token` returns `"*"` for None or empty; test asserts |
| T-03-03-02: Crash mid-processing re-fetch loop | Mitigated — `save_sync_token` before item loop; `test_startup_sync_persists_token_before_processing` enforces ordering |
| T-03-03-03: API token on app.state | Accepted — process-local, no endpoint exposes it |
| T-03-03-04: Silent TodoistAuthError swallow | Mitigated — lifespan does not catch; `test_lifespan_startup_sync_failure_propagates` confirms |
| T-03-03-05: Items from other projects | Mitigated — `project_id=settings.todoist_project_id` passed to `fetch_sync_delta` (Plan 02 client-side filter) |
| T-03-03-06: httpx client leak on boot failure | Mitigated — `try/except` around `startup_sync` closes client before re-raising |

## Known Stubs

None. `startup_sync` counts and logs processed items but does not hand them off — this is intentional (Phase 3 is read-only; Phase 5 wires the sync pipeline).

## Threat Flags

None — no new network endpoints, auth paths, or schema changes beyond what the plan specified.

## Self-Check: PASSED

- app/todoist/sync_manager.py: FOUND (contains KV_SYNC_TOKEN_KEY, load_sync_token, save_sync_token, startup_sync, upsert_kv import, no upsert_kv definition)
- tests/unit/test_todoist_sync_manager.py: FOUND (9 test functions)
- app/main.py: FOUND (TodoistClient import, startup_sync import, await startup_sync before yield at line 102 < yield at line 108, close after yield at line 117)
- Commit 7cd1676: FOUND
- Commit da9d498: FOUND
- Commit aa676a2: FOUND
- Commit 4fb3e6d: FOUND
- pytest tests/ -q: 162 passed
