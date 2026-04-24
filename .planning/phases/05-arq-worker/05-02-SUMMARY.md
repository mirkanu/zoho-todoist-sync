---
phase: 05-arq-worker
plan: "02"
subsystem: worker
tags: [arq, worker, redis, lifecycle, railway, tdd]
dependency_graph:
  requires:
    - app/worker/__init__.py
    - app/worker/jobs.py
    - app/core/config.py
    - app/core/logging.py
    - app/zoho/client.py
    - app/zoho/state.py
    - app/zoho/token_manager.py
    - app/todoist/client.py
  provides:
    - app/worker/settings.py
    - app/worker/__main__.py
    - app/worker/enqueue.py
  affects:
    - Phase 6 (webhook handlers call enqueue_sync)
    - Phase 7 (reconciler calls enqueue_sync)
tech_stack:
  added: []
  patterns:
    - arq WorkerSettings class with on_startup/on_shutdown hooks
    - arq func() with timeout_s/keep_result_s/max_tries
    - RedisSettings.from_dsn for Redis connection
    - asyncio.create_task for proactive_refresh_loop background task
    - CancelledError swallowed in on_shutdown for graceful task cancellation
    - enqueue_sync with _job_id dedup and _defer_by forwarding
key_files:
  created:
    - app/worker/settings.py
    - app/worker/__main__.py
    - app/worker/enqueue.py
    - tests/unit/test_worker_settings.py
  modified: []
decisions:
  - "arq Function stores timeout as timeout_s and keep_result as keep_result_s (in seconds) — test assertions must use fn.timeout_s/fn.keep_result_s, not fn.timeout/fn.keep_result"
  - "importlib.reload(ws_mod) in each test ensures WorkerSettings class-level get_settings() evaluates fresh with test env vars"
  - "_clear_settings_cache fixture depends on complete_env to ensure env vars are set before cache_clear() is called"
metrics:
  duration_minutes: 10
  completed_date: "2026-04-24"
  tasks_completed: 2
  files_created: 4
---

# Phase 5 Plan 02: Worker Lifecycle Scaffold Summary

**One-liner:** arq WorkerSettings with on_startup/on_shutdown lifecycle (DB engine, Zoho token load/refresh, proactive_refresh_loop background task) and enqueue_sync helper with `_job_id` dedup and `_defer_by` forwarding.

## What Was Built

### `app/worker/settings.py` (107 lines)
arq worker lifecycle configuration:

- **`on_startup(ctx)`**: Configures logging, sets `resend.api_key`, builds SQLAlchemy async engine + session_factory, loads Zoho token from kv_store (refreshes if missing/expired, upserts both keys), publishes to `token_state`, constructs `ZohoClient` + `TodoistClient`, and launches `proactive_refresh_loop` as `asyncio.create_task` stored in `ctx['_refresh_task']` (mitigates T-05-15 — worker Zoho token expiry).
- **`on_shutdown(ctx)`**: Cancels `ctx['_refresh_task']` (awaiting with `CancelledError` swallowed), closes TodoistClient httpx pool, disposes SQLAlchemy engine. Tolerates missing `_refresh_task`.
- **`WorkerSettings`**: `functions = [func(sync_task, timeout=60, keep_result=300, max_tries=4)]`, `redis_settings = RedisSettings.from_dsn(get_settings().redis_url)`, `max_jobs = 10`.

### `app/worker/__main__.py` (6 lines)
Minimal Railway entry point: `run_worker(WorkerSettings)` under `if __name__ == "__main__"`.

### `app/worker/enqueue.py` (45 lines)
`enqueue_sync(redis, zoho_task_id, defer_secs=0)` — the single enqueue entry point for Phase 6 webhook handlers and Phase 7 reconciler:
- Passes `_job_id=f"sync:{zoho_task_id}"` for arq dedup (SYNC-10)
- Forwards `defer_secs` verbatim as `_defer_by` (LOOP-4)
- Emits `log.warning("sync_task_dedup_dropped", ...)` when `enqueue_job` returns `None`
- Callers (Phase 6) are responsible for passing `settings.zoho_job_defer_secs` for Zoho-triggered jobs

### `tests/unit/test_worker_settings.py` (314 lines)
12 unit tests covering all specified behaviours:

| Test | Requirement |
|------|-------------|
| `test_redis_settings_from_dsn` | INFRA-3 |
| `test_sync_task_registered_with_correct_func_config` | func config |
| `test_worker_settings_has_on_startup_and_on_shutdown` | lifecycle hooks |
| `test_worker_settings_max_jobs_default` | max_jobs=10 |
| `test_on_startup_populates_ctx` | INFRA-1 |
| `test_on_startup_launches_proactive_refresh_loop` | Pitfall 7 / T-05-15 |
| `test_on_shutdown_cancels_refresh_task_and_closes_clients` | graceful shutdown |
| `test_on_shutdown_tolerates_missing_refresh_task` | robustness |
| `test_enqueue_sync_dedups` | SYNC-10 |
| `test_enqueue_sync_defers_by_zoho_secs` | LOOP-4 |
| `test_enqueue_sync_default_defer_is_zero` | default behavior |
| `test_main_module_calls_run_worker` | INFRA-1 entry point |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed arq Function attribute names for timeout and keep_result**
- **Found during:** Task 2 (GREEN phase)
- **Issue:** Plan specified `fn.timeout == 60` and `fn.keep_result == 300` but arq's `Function` dataclass stores these as `fn.timeout_s` (timeout in seconds) and `fn.keep_result_s` (keep_result in seconds), not `fn.timeout`/`fn.keep_result`. This is the same class of issue as the Plan 01 `defer_score` deviation.
- **Fix:** Updated test assertions to `fn.timeout_s == 60` and `fn.keep_result_s == 300`
- **Files modified:** `tests/unit/test_worker_settings.py`
- **Commit:** 862a99c

**2. [Rule 3 - Blocking] Fixed autouse fixture ordering with complete_env dependency**
- **Found during:** Task 1 (RED phase)
- **Issue:** `_clear_settings_cache(autouse=True)` fixture was importing `get_settings` before `complete_env` set env vars, causing `ValidationError` during fixture setup. pytest runs autouse fixtures before per-test fixtures unless the autouse fixture depends on the per-test fixture.
- **Fix:** Added `complete_env` as a parameter to `_clear_settings_cache` to ensure env vars are set before `get_settings.cache_clear()` is called.
- **Files modified:** `tests/unit/test_worker_settings.py`
- **Commit:** 21236a2 (part of RED gate commit)

## TDD Gate Compliance

- RED gate: `test(05-02)` commit `21236a2` — 12 tests collected, all failing with `ModuleNotFoundError: No module named 'app.worker.settings'` / `FileNotFoundError: app/worker/__main__.py`
- GREEN gate: `feat(05-02)` commit `862a99c` — all 12 tests pass; full suite (215 tests) green

## Known Stubs

None — all code paths are fully implemented. `WorkerSettings` is complete; `enqueue_sync` is wired to real arq interfaces.

## Threat Flags

No new threat surface beyond what the plan's threat model covers. All STRIDE mitigations T-05-08 through T-05-15 are implemented:
- T-05-09: `on_startup` logs only `log_level` — never secrets, never `database_url`
- T-05-10: `resend.api_key` set via module assignment, not logged
- T-05-11: `max_jobs = 10` explicitly set
- T-05-13: `enqueue_sync` emits `log.warning("sync_task_dedup_dropped", ...)` on None return
- T-05-15: `proactive_refresh_loop` launched as `asyncio.create_task` in `on_startup`; cancelled in `on_shutdown`

## Self-Check: PASSED

| Item | Status |
|------|--------|
| `app/worker/settings.py` | FOUND |
| `app/worker/__main__.py` | FOUND |
| `app/worker/enqueue.py` | FOUND |
| `tests/unit/test_worker_settings.py` | FOUND |
| Commit `21236a2` (RED gate) | FOUND |
| Commit `862a99c` (GREEN gate) | FOUND |
| All 12 unit tests pass | PASSED |
| Full suite (215 tests) green | PASSED |
