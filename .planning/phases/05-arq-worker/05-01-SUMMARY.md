---
phase: 05-arq-worker
plan: "01"
subsystem: worker
tags: [arq, worker, sync, redis, postgres, tdd]
dependency_graph:
  requires:
    - app/core/hash.py
    - app/core/normalise.py
    - app/db/models.py
    - app/zoho/client.py
    - app/zoho/normalise.py
    - app/zoho/writer.py
    - app/zoho/state.py
    - app/todoist/client.py
    - app/todoist/normalise.py
    - app/todoist/writer.py
  provides:
    - app/worker/__init__.py
    - app/worker/jobs.py
  affects:
    - Plan 05-02 (WorkerSettings imports sync_task from jobs.py)
tech_stack:
  added: []
  patterns:
    - arq Retry with defer_score-based backoff (RETRY_DELAYS)
    - Redis SETNX lock via set(nx=True, ex=30) in try/finally
    - SQLAlchemy async SELECT FOR UPDATE inside session.begin()
    - External API fetches before FOR UPDATE critical section (Pitfall 2 prevention)
    - LWW conflict resolution with Zoho as source of truth (SYNC-11)
key_files:
  created:
    - app/worker/__init__.py
    - app/worker/jobs.py
    - tests/unit/test_worker_jobs.py
  modified: []
decisions:
  - "arq Retry stores defer time as defer_score (milliseconds), not a 'defer' attribute in seconds — test assertions use defer_score / 1000"
  - "API fetches (zoho_client.get_task, todoist_client.get_task) placed before session.begin() per T-05-03 threat mitigation"
  - "_apply_write called inside session.begin() for DB+event atomicity — acceptable trade-off since write duration is bounded"
  - "ZohoNotFoundError and TodoistNotFoundError do NOT raise Retry — log and return to avoid burning retry budget on permanent failures"
metrics:
  duration_minutes: 6
  completed_date: "2026-04-24"
  tasks_completed: 2
  files_created: 3
---

# Phase 5 Plan 01: sync_task Pipeline Summary

**One-liner:** `sync_task` arq job with SETNX Redis lock, SELECT FOR UPDATE, LWW conflict resolution, echo suppression, and exponential retry backoff — wires all prior phase modules into a single reliable sync unit.

## What Was Built

### `app/worker/__init__.py`
Package marker for the `app.worker` module.

### `app/worker/jobs.py` (249 lines)
Core `sync_task` arq job function implementing the full Zoho <-> Todoist sync pipeline:

1. **SETNX Redis lock** (`lock:sync:{id}`, 30s TTL, try/finally release) — defence-in-depth against concurrent jobs for the same task (T-05-02)
2. **Live Zoho fetch** via `zoho_client.get_task()` + `zoho_record_to_normalised()` — happens BEFORE any DB lock (T-05-03)
3. **sync_state lookup** (unlocked read) — routes to new-task path or update path
4. **New task path** (`_handle_new_task`): `create_todoist_task` + `write_todoist_id_to_zoho` + `SyncState` insert + `SyncEvent(action='sync')`
5. **Live Todoist fetch** via `todoist_client.get_task()` — also before lock
6. **Canonical hash computation** on both normalised views
7. **SELECT FOR UPDATE critical section**: re-read `last_hash`, compare hashes:
   - Both match → `echo_suppressed` (LOOP-1)
   - Both differ → `overwrite`, Zoho wins (SYNC-11 LWW)
   - Only Zoho differs → `sync`, direction=`zoho_to_todoist`
   - Only Todoist differs → `sync`, direction=`todoist_to_zoho`
8. **Write routing** (`_apply_write`): `complete_*` vs `update_*` based on `is_completed`
9. **Retry backoff**: `RETRY_DELAYS = {1: 5, 2: 15, 3: 60}` seconds; `ZohoNotFoundError` / `TodoistNotFoundError` do NOT retry

### `tests/unit/test_worker_jobs.py` (490 lines)
11 unit tests covering all specified behaviours:

| Test | Requirement |
|------|-------------|
| `test_sync_task_lock_not_acquired_returns_early` | SETNX guard |
| `test_sync_task_lock_released_in_finally` | T-05-02 |
| `test_echo_suppressed_when_all_hashes_match` | LOOP-1 |
| `test_select_for_update_is_called` | LOOP-3 |
| `test_new_task_creates_todoist_and_writes_id_back` | new-task flow |
| `test_bootstrap_race_footer_suppressed` | LOOP-5 |
| `test_lww_zoho_wins_when_both_diverge` | SYNC-11 |
| `test_zoho_hash_differs_writes_to_todoist` | zoho->todoist direction |
| `test_todoist_hash_differs_writes_to_zoho` | todoist->zoho direction |
| `test_retry_on_zoho_rate_limit_uses_correct_delay` | retry backoff |
| `test_completion_routes_to_complete_not_update` | complete vs update routing |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test assertion for arq Retry delay attribute**
- **Found during:** Task 2 (GREEN phase)
- **Issue:** Plan specified `exc_info.value.defer == expected_delay` but arq `Retry` stores the delay as `defer_score` in milliseconds (e.g., `defer_score=5000` for 5 seconds), not as a `defer` attribute in seconds.
- **Fix:** Updated test assertion to `exc_info.value.defer_score / 1000 == expected_delay`
- **Files modified:** `tests/unit/test_worker_jobs.py`
- **Commit:** 053ed3f

## TDD Gate Compliance

- RED gate: `test(05-01)` commit `2539e58` — 11 tests collected, all failing with `ModuleNotFoundError: No module named 'app.worker.jobs'`
- GREEN gate: `feat(05-01)` commit `053ed3f` — all 11 tests pass; full suite (203 tests) green

## Known Stubs

None — all code paths are fully implemented. `sync_task` is importable and wired to real module interfaces.

## Threat Flags

No new threat surface introduced beyond what the plan's threat model already covers. All STRIDE mitigations from T-05-01 through T-05-07 are implemented:
- T-05-01: `zoho_task_id` passed to parameterised SQLAlchemy `select()` only
- T-05-02: 30s TTL + try/finally on SETNX lock
- T-05-03: API fetches occur before `session.begin()` at lines 118 and 133 in jobs.py
- T-05-05: Only `zoho_task_id`, `action`, `direction`, 8-char hash prefix logged — no full task bodies
- T-05-06: Every write path inserts a `SyncEvent` row inside the same transaction
- T-05-07: `token_state["access_token"]` read but never logged

## Self-Check: PASSED

| Item | Status |
|------|--------|
| `app/worker/__init__.py` | FOUND |
| `app/worker/jobs.py` | FOUND |
| `tests/unit/test_worker_jobs.py` | FOUND |
| `05-01-SUMMARY.md` | FOUND |
| Commit `2539e58` (RED gate) | FOUND |
| Commit `053ed3f` (GREEN gate) | FOUND |
| All 11 unit tests pass | PASSED |
| Full suite (203 tests) green | PASSED |
