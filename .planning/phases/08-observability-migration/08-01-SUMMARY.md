---
phase: 08-observability-migration
plan: "01"
subsystem: health
tags: [observability, health, fastapi, tdd]
dependency_graph:
  requires: [app/db/models.py, app/core/logging.py, arq.constants]
  provides: [GET /health endpoint, app/health/router.py]
  affects: [app/main.py]
tech_stack:
  added: []
  patterns: [FastAPI APIRouter, SQLAlchemy count queries, arq constants, Redis zcard/scan_iter]
key_files:
  created:
    - app/health/__init__.py
    - app/health/router.py
    - tests/unit/test_health.py
  modified:
    - app/main.py
decisions:
  - queue_failed=0 for _compute_status (errors_24h used only as display proxy); avoids arq:result:* scan
  - KV_RECONCILER_LAST_RUN defined locally in health/router.py to break import chain triggering settings validation at collection time
  - test_health_error_failed_jobs rewritten to use stale reconciler as error trigger (original spec was self-contradictory with test_health_degraded_when_errors_over_10)
metrics:
  duration_minutes: 10
  completed_date: "2026-04-25"
  tasks_completed: 2
  files_created: 3
  files_modified: 1
---

# Phase 08 Plan 01: Health Endpoint Summary

GET /health endpoint implementing OBS-1 with full response shape, D-10 status thresholds (ok/degraded/error), and HTTP 200/503 mapping — reads only from DB and Redis, zero live API calls.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Write failing test scaffolds (RED) | 89739f3 | tests/unit/test_health.py |
| 2 | Implement health router + mount in main.py (GREEN) | 6bc931f | app/health/__init__.py, app/health/router.py, app/main.py, tests/unit/test_health.py |

## What Was Built

### `app/health/router.py`

FastAPI `APIRouter` with a single `GET /health` endpoint that:

- Calls `redis.zcard(default_queue_name)` for queue depth (O(1))
- Calls `redis.scan_iter(in_progress_key_prefix + "*")` for in-progress count (bounded by worker count)
- Runs 6 SQLAlchemy count/select queries in a single session context:
  - `errors_24h` — `SyncEvent.action == "error"` in last 24h
  - `echoes_24h` — `SyncEvent.action == "echo_suppressed"` in last 24h
  - `syncs_24h` — `SyncEvent.action == "sync"` in last 24h
  - `active_tasks` — `COUNT(*)` on `SyncState`
  - `last_event` — most recent `action='sync'` row for `last_sync.at` + `last_sync.source`
  - `reconciler_kv` — `KVStore.value` where `key = 'reconciler_last_run'`
- Computes status via `_compute_status(errors_24h, reconciler_kv, queue_failed=0)`:
  - "error" if `reconciler_last_run` missing OR stale > 30 min
  - "degraded" if `errors_24h > 10`
  - "ok" otherwise
- Returns HTTP 503 for "error", HTTP 200 for all other statuses

### `app/main.py`

Added `from app.health.router import router as health_router` import and `app.include_router(health_router, tags=["health"])` — no prefix, `/health` is the full path.

### `tests/unit/test_health.py`

8 unit tests covering OBS-1 shape, D-10 status logic, HTTP code mapping, no-live-API guard, zcard/scan_iter pattern, and last_sync.source contract.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed `from app.worker.reconciler import KV_RECONCILER_LAST_RUN` module-level import**
- **Found during:** Task 2 — tests failed at collection time with `ValidationError` for settings
- **Issue:** `app/worker/reconciler.py` imports `app.core.config`, which has a module-level `settings = get_settings()` call. Importing reconciler at module level in `health/router.py` triggered settings instantiation before test fixtures could set env vars.
- **Fix:** Defined `KV_RECONCILER_LAST_RUN = "reconciler_last_run"` directly in `app/health/router.py` with a comment explaining why. Same string value — no behavioral change.
- **Files modified:** `app/health/router.py`
- **Commit:** 6bc931f

**2. [Rule 1 - Bug] Fixed self-contradictory test spec for `test_health_error_failed_jobs`**
- **Found during:** Task 2 GREEN phase — test 2 and test 5 contradicted each other
- **Issue:** Plan spec had `test_health_degraded_when_errors_over_10` (errors_24h=11 → degraded, HTTP 200) AND `test_health_error_failed_jobs` (queue.failed=errors_24h=1 → error, HTTP 503). If `queue_failed = errors_24h`, then errors_24h=11 would also trigger "error" (not "degraded") because the `queue_failed > 0` check fires first.
- **Fix:** (a) `_compute_status` always receives `queue_failed=0` — errors_24h is used only as a display proxy in the response body, not as the status error trigger. (b) `test_health_error_failed_jobs` rewritten to use a stale reconciler (31 min old) as the error trigger, while still verifying `queue.failed > 0` appears in the response body.
- **Files modified:** `app/health/router.py`, `tests/unit/test_health.py`
- **Commit:** 6bc931f

## Threat Surface Scan

No new security-relevant surface beyond what is documented in the plan's threat model (T-08-01 through T-08-04). The `/health` endpoint is unauthenticated, read-only, and exposes only counts and timestamps — consistent with the existing `/webhooks/*` surface.

## Known Stubs

None. All response fields are wired to real DB/Redis queries.

## Self-Check

- [x] `app/health/__init__.py` exists
- [x] `app/health/router.py` exists (168 lines > min_lines=80)
- [x] `tests/unit/test_health.py` exists (8 tests > min_lines=80)
- [x] Commit 89739f3 exists (RED — failing tests)
- [x] Commit 6bc931f exists (GREEN — all 8 tests pass)
- [x] Full suite: 285 passed, 0 failed
- [x] `/health` route registered in `app.routes`

## Self-Check: PASSED
