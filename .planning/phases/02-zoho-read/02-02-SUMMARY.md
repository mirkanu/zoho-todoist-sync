---
phase: 02-zoho-read
plan: "02"
subsystem: zoho-token-lifecycle
tags: [zoho, oauth, token-refresh, fastapi, lifespan, asyncio, tdd, unit-tests]
one_liner: "Proactive 50-min Zoho OAuth refresh loop with kv_store persistence, asyncio background task lifecycle wired into FastAPI lifespan alongside startup field metadata resolution"
dependency_graph:
  requires: [app/zoho/client.py, app/db/models.py, app/core/config.py, app/core/logging.py]
  provides: [app/zoho/token_manager.py, app/zoho/state.py, modified app/main.py]
  affects: [Phase 03+ sync worker, Phase 05 arq enqueue (LOOP-4 plumbing), all components reading token_state/zoho_field_cache]
tech_stack:
  added: []
  patterns: [from __future__ import annotations + TYPE_CHECKING for lazy config import, module-self import pattern for monkeypatch isolation, dual stdlib+structlog warnings for caplog testability]
key_files:
  created:
    - app/zoho/state.py
    - app/zoho/token_manager.py
    - tests/unit/test_token_manager.py
    - tests/unit/test_main_lifespan.py
  modified:
    - app/main.py
decisions:
  - "TYPE_CHECKING guard + from __future__ import annotations for Settings type hint: avoids triggering module-level settings = get_settings() at import time when env vars are absent in tests."
  - "import app.zoho.token_manager as _self inside proactive_refresh_loop: prevents stale closure reference after sys.modules purge in test_main_py_importable_with_env; loop always resolves refresh_access_token from the current module dict at call time."
  - "Dual stdlib + structlog warning calls in main.py: structlog (PrintLoggerFactory) writes to stdout only; caplog captures only stdlib logging calls. Both calls ensure production JSON logs and pytest caplog assertions both work."
  - "REFRESH_INTERVAL_SECS = 50 * 60 (3000s): 10-minute safety margin on 60-minute Zoho token lifetime per RESEARCH.md Pattern 2."
metrics:
  duration_seconds: 2400
  completed_date: "2026-04-23"
  tasks_completed: 2
  files_created: 4
  files_modified: 1
  tests_added: 16
  total_unit_tests: 124
---

# Phase 2 Plan 2: Token Manager + Lifespan Wiring Summary

Proactive Zoho OAuth refresh loop running every 50 minutes as an asyncio background task, with kv_store persistence and re-raise-on-failure semantics (INFRA-6), wired into FastAPI lifespan alongside startup field metadata resolution and shared state dict population (INFRA-7). LOOP-4 plumbing (ZOHO_JOB_DEFER_SECS via settings) exposed for Phase 5.

## Files Created

| File | Purpose |
|------|---------|
| `app/zoho/state.py` | Mutable module-level singletons: `token_state` and `zoho_field_cache` |
| `app/zoho/token_manager.py` | `refresh_access_token`, `proactive_refresh_loop`, `upsert_kv`, `load_token_from_kv` |
| `tests/unit/test_token_manager.py` | 9 async tests: refresh contract, error paths, loop behavior, no-token-logging |
| `tests/unit/test_main_lifespan.py` | 7 async tests: lifespan startup sequences, field resolution, WARN events, task lifecycle |

## Files Modified

| File | Changes |
|------|---------|
| `app/main.py` | Complete rewrite of lifespan: token load/refresh, field metadata resolution, asyncio task, shutdown cleanup |

## Functions Implemented

| Function | Module | Description |
|----------|--------|-------------|
| `refresh_access_token(settings)` | token_manager | POST to accounts.zoho.eu, raises RuntimeError on any failure |
| `upsert_kv(session, key, value)` | token_manager | Idempotent kv_store insert-or-update |
| `load_token_from_kv(session)` | token_manager | Load persisted token + expiry; None if absent or corrupt |
| `proactive_refresh_loop(token_state, session_factory)` | token_manager | asyncio.Task body: sleep 50 min → refresh → persist → repeat; re-raises on failure |

## Constants Exported

| Constant | Value | Purpose |
|----------|-------|---------|
| `REFRESH_INTERVAL_SECS` | 3000 | 50-minute refresh interval (Phase 5 can read this) |
| `ACCOUNTS_URL_EU` | `https://accounts.zoho.eu/oauth/v2/token` | OAuth token endpoint |
| `KV_ACCESS_TOKEN_KEY` | `zoho_access_token` | kv_store row key for token value |
| `KV_EXPIRES_AT_KEY` | `zoho_token_expires_at` | kv_store row key for expiry ISO string |

## Shared State Dicts (app/zoho/state.py)

| Name | Type | Populated by | Read by |
|------|------|-------------|---------|
| `token_state` | `TokenState` TypedDict | lifespan startup + proactive_refresh_loop | Phase 3+ sync jobs (future) |
| `zoho_field_cache` | `ZohoFieldCache` TypedDict | lifespan startup | Phase 3+ field-name lookups (future) |

Both are CPython-atomic dict assignments — single writer (refresh task), many readers.

## Lifespan Startup Sequence

1. Create async DB engine + session_factory from `settings.database_url`
2. Load `(stored_token, stored_expires_at)` from kv_store
3. Refresh if token is missing, None expires_at, or expires_at <= now_utc
4. Persist fresh token to kv_store (both value + expiry rows)
5. Populate `token_state["access_token"]` and `token_state["expires_at"]`
6. Call `ZohoClient.get_fields_metadata("Tasks")` → populate `zoho_field_cache`
7. WARN `zoho_todoist_task_id_field_not_found` if api_name is None
8. WARN `zoho_terminal_status_not_in_picklist` for each configured status absent from live picklist (Pitfall 5 safeguard)
9. `asyncio.create_task(proactive_refresh_loop(...))` → store on `app.state.zoho_refresh_task`

## Lifespan Shutdown Sequence

1. Cancel refresh task
2. Await task (suppress CancelledError + any Exception)
3. Dispose DB engine
4. Log "shutdown"

## How Phase 5 Consumes These

- `token_state["access_token"]` — inject into `ZohoClient(access_token=token_state["access_token"])` per arq job
- `zoho_field_cache["todoist_task_id_api_name"]` — use as the Zoho custom field api_name for Todoist ID storage/lookup
- `zoho_field_cache["status_picklist_values"]` — validate incoming Zoho status values
- `settings.zoho_job_defer_secs` (LOOP-4) — pass to arq enqueue as `_defer_by` seconds

## Test Count

- 9 async tests in `test_token_manager.py`
- 7 async tests in `test_main_lifespan.py`
- Full unit suite: **124 tests pass** (Phase 1 + Plan 01 + Plan 02, no regressions)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Module-level config import triggered ValidationError in tests**
- **Found during:** Task 1 test execution (GREEN phase)
- **Issue:** `from app.core.config import Settings, get_settings` at module level triggers the `settings = get_settings()` alias in config.py, which raises `ValidationError` when env vars are absent (normal in test collection). The plan's provided code used a direct module-level import.
- **Fix:** Used `from __future__ import annotations` to make type hints lazy (strings), moved `Settings` under `TYPE_CHECKING` guard (import only during type analysis, not at runtime). `get_settings()` inside `proactive_refresh_loop` uses a local import.
- **Files modified:** `app/zoho/token_manager.py`
- **Commit:** df6631d

**2. [Rule 1 - Bug] sys.modules purge in test_main_py_importable_with_env broke monkeypatch**
- **Found during:** Task 1 full suite run — `test_proactive_refresh_loop_updates_token_state` and `test_proactive_refresh_loop_reraises_on_failure` failed when run after `test_migration_and_app.py`
- **Issue:** `test_main_py_importable_with_env` deletes all `app.*` from `sys.modules` and re-imports, creating a new module object for `app.zoho.token_manager`. The test file's top-level `from app.zoho.token_manager import proactive_refresh_loop` captured the OLD function (from the pre-purge module). `monkeypatch.setattr("app.zoho.token_manager.refresh_access_token", ...)` patched the NEW module, but `proactive_refresh_loop.__globals__` pointed to the OLD module dict — so the patch didn't take effect, and the real function made a live HTTP call to Zoho.
- **Fix:** Inside `proactive_refresh_loop`, replaced direct calls to `refresh_access_token(...)` and `upsert_kv(...)` with `import app.zoho.token_manager as _self; _self.refresh_access_token(...)` / `_self.upsert_kv(...)`. This resolves the current module object from `sys.modules` at call time, not from the stale captured closure — so monkeypatch always takes effect.
- **Files modified:** `app/zoho/token_manager.py`
- **Commit:** df6631d

**3. [Rule 1 - Bug] structlog PrintLoggerFactory bypasses caplog — WARN tests failed**
- **Found during:** Task 2 test execution — `test_lifespan_logs_warn_when_terminal_status_missing_from_picklist` and `test_lifespan_logs_warn_when_todoist_field_missing` failed; warning appeared in captured stdout but not in `caplog.records`
- **Issue:** structlog configured with `PrintLoggerFactory()` writes directly to stdout; Python's `caplog` fixture only intercepts stdlib `logging` module calls. The plan's `log.warning(...)` calls went only to stdout.
- **Fix:** Added `import logging as _stdlib_logging` and `_log = _stdlib_logging.getLogger(__name__)` to `app/main.py`. Both WARN events now call `_log.warning(event_name)` (for caplog) AND `log.warning(event_name, ...)` (for production JSON output via structlog). Dual-call pattern preserves production observability while enabling test assertions.
- **Files modified:** `app/main.py`
- **Commit:** d1bea2c

## Threat Surface Scan

All mitigations from the plan's threat model (T-02-08 through T-02-15) confirmed applied:

| Threat | Mitigation | Status |
|--------|-----------|--------|
| T-02-08: Refresh token logged | Never passed to log calls; only settings fields used in POST body | Confirmed |
| T-02-09: Access token in logs | `log.info("zoho_token_refreshed", expires_at=..., expires_in=...)` — no token value | Confirmed |
| T-02-10: Silent refresh retry | `proactive_refresh_loop` re-raises on any exception; test verifies | Confirmed |
| T-02-14: kv_store corrupt expires_at | `datetime.fromisoformat` wrapped in try/except ValueError → returns None → triggers refresh | Confirmed |
| T-02-15: Refresh audit trail | Every refresh logs `zoho_token_refreshed`; every failure logs `zoho_token_refresh_failed` | Confirmed |

No new threat surface introduced beyond the plan's scope.

## Open Items for Phase 3

- `token_state` and `zoho_field_cache` are populated at startup and read by sync jobs — Phase 3 will add the arq worker that reads `token_state["access_token"]` to construct `ZohoClient` instances per job
- `zoho_field_cache["todoist_task_id_api_name"]` will be used in Phase 3's Zoho-to-Todoist sync to look up the custom field value
- The `ZOHO_JOB_DEFER_SECS` / `settings.zoho_job_defer_secs` constant is wired (LOOP-4) and available for Phase 5's arq enqueue `_defer_by` parameter

## Self-Check: PASSED
