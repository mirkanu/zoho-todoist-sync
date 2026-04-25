---
phase: 07-reconciliation-orphan-detection
plan: "02"
subsystem: worker-reconciler
tags: [orphan-sweep, cron, seed-6, edge-1, edge-2, edge-5, edge-6, edge-8, tdd]
dependency_graph:
  requires:
    - app/worker/reconciler.py        # reconcile_sweep (Plan 01)
    - app/todoist/writer.py           # delete_todoist_task
    - app/zoho/writer.py              # delete_zoho_task
    - app/core/notifications.py       # send_deletion_notification
    - app/zoho/state.py               # token_state
    - app/db/models.py                # SyncState, SyncEvent
    - app/zoho/client.py              # ZohoNotFoundError, ZohoRateLimitError, ZohoAPIError
    - app/todoist/client.py           # TodoistNotFoundError, TodoistRateLimitError, TodoistAPIError
  provides:
    - app/worker/reconciler.py        # orphan_sweep, _handle_orphan, KV_ORPHAN_SWEEP_LAST_RUN
    - app/worker/settings.py          # WorkerSettings.cron_jobs registration
  affects:
    - tests/unit/test_reconciler.py   # 9 new orphan tests appended
    - tests/unit/test_worker_settings.py # test_cron_jobs_registered appended
tech_stack:
  added: []
  patterns:
    - arq cron function (ctx dict, no job_try)
    - two-cycle orphan confirmation via orphan_check_count column
    - session.begin() transactional writes for row mutations
    - skip-on-rate-limit pattern (continue, not Retry)
    - fire-and-forget deletion via existing writers (notifications internal to writers)
key_files:
  created: []
  modified:
    - app/worker/reconciler.py        # 183 lines → 281 lines (+98 lines)
    - app/worker/settings.py          # 120 lines → 125 lines (+5 lines)
    - tests/unit/test_reconciler.py   # 403 lines → 762 lines (+359 lines)
    - tests/unit/test_worker_settings.py  # 319 lines → 346 lines (+27 lines)
decisions:
  - "delete_todoist_task and delete_zoho_task already call send_deletion_notification internally (Phase 4 EDGE-6). _handle_orphan does NOT call it again — no double-notification. Tests patched send_deletion_notification but do not assert it called (assertion would be wrong)."
  - "arq CronJob stores minute as a set, second as an int (not a set). Test assertions updated to use in (0, {0}) pattern to be version-tolerant. timeout stored as timeout_s."
  - "orphan_sweep uses session.begin() for all row mutations (increment, reset, delete) per project-standard transactional pattern from PATTERNS.md."
  - "Rate-limit errors (ZohoRateLimitError, ZohoAPIError, TodoistRateLimitError, TodoistAPIError) skip the row via continue — not counted as orphan detection. Prevents false deletion from transient API errors (T-7-01 mitigation)."
  - "EDGE-8 footer re-attachment is purely additive: existing description + footer suffix. extract_zoho_id checked first — if any valid footer present, no re-attachment."
metrics:
  duration_minutes: 10
  completed_date: "2026-04-25"
  tasks_completed: 2
  files_created: 0
  files_modified: 4
  tests_added: 10
  tests_total_after: 260
---

# Phase 7 Plan 02: orphan_sweep + cron_jobs Registration Summary

**One-liner:** orphan_sweep hourly cron with two-cycle confirmation, reassignment detection, Todoist/Zoho cross-deletion, missing-footer re-attachment, and both crons registered in WorkerSettings.cron_jobs.

## Files Modified

| File | Lines Before | Lines After | Delta | Purpose |
|------|-------------|-------------|-------|---------|
| `app/worker/reconciler.py` | 99 | 281 | +182 | orphan_sweep + _handle_orphan + KV constant |
| `app/worker/settings.py` | 120 | 125 | +5 | cron_jobs attribute + import cron + reconciler funcs |
| `tests/unit/test_reconciler.py` | 403 | 762 | +359 | 9 orphan tests + helpers |
| `tests/unit/test_worker_settings.py` | 319 | 346 | +27 | test_cron_jobs_registered |

## Test Results

- `python3 -m pytest tests/unit/test_reconciler.py -q` → **19 passed** (10 reconcile + 9 orphan)
- `python3 -m pytest tests/unit/test_worker_settings.py -q` → **13 passed** (includes test_cron_jobs_registered)
- `python3 -m pytest tests/ -x -q` → **260 passed, 3 warnings** (no regressions; warnings are pre-existing)

## Implementation Notes

### orphan_sweep Algorithm

1. Load all `sync_state` rows via `select(SyncState)` (full table scan, once per sweep).
2. For each row:
   a. **Zoho check**: `zoho_client.get_task(zoho_task_id)` → verify `Owner.id == settings.zoho_user_id`. ZohoNotFoundError or Owner mismatch → `zoho_missing = True`. ZohoRateLimitError/ZohoAPIError → `continue` (skip row, retry next hour).
   b. **Todoist check**: `todoist_client.fetch_todoist_task(todoist_task_id)`. TodoistNotFoundError → `todoist_missing = True`. TodoistRateLimitError/TodoistAPIError → `continue`.
   c. **Healthy path**: both present → EDGE-8 re-attach footer if missing; reset `orphan_check_count` to 0 if elevated; `continue`.
   d. **First cycle** (`orphan_check_count < 1`): log WARN, increment count in `session.begin()`, `continue`.
   e. **Second cycle**: call `_handle_orphan(state, zoho_missing, todoist_missing, ctx)`.
3. Upsert `KV_ORPHAN_SWEEP_LAST_RUN` with ISO-8601 UTC timestamp; commit.

### _handle_orphan Algorithm

- If `zoho_missing`: `delete_todoist_task(todoist_task_id, ctx["todoist_client"]._api)` — sends notification internally.
- If `todoist_missing`: `delete_zoho_task(zoho_task_id, token_state["access_token"])` — sends notification internally.
- In `session.begin()`: delete `SyncState` row, `session.add(SyncEvent(action='orphan', source='reconciler', ...))`.

### Key Finding: Notification Already Sent by Writers

`delete_todoist_task` (writer.py line 109-113) and `delete_zoho_task` (writer.py line 107-111) both call `send_deletion_notification` internally after a successful deletion. `_handle_orphan` does NOT call it again — this prevents double-notification. The test mocks patch both the writer functions AND `send_deletion_notification` but do not assert the latter was called directly.

### arq CronJob Attribute Names (for future cron tests)

| Attribute | Type | Notes |
|-----------|------|-------|
| `minute` | `set` | e.g. `{0, 15, 30, 45}` |
| `second` | `int` | e.g. `0` (not a set) |
| `timeout_s` | `int` | timeout in seconds (not `timeout`) |
| `coroutine` | callable | the wrapped async function |

## Requirement Coverage

| Requirement | Plan | Status |
|-------------|------|--------|
| SEED-5 | Plan 01 | Satisfied (reconcile_sweep) |
| SEED-6 | Plan 02 | Satisfied (orphan_sweep) |
| SEED-7 | Plan 01 | Satisfied (sync_token crash-safe) |
| LOOP-3 | Plan 02 | Satisfied (session.begin() per-row mutations) |
| EDGE-1 | Plan 02 | Satisfied (Owner.id mismatch → Todoist delete on 2nd cycle) |
| EDGE-2 | Plan 02 | Satisfied (TodoistNotFoundError → Zoho delete on 2nd cycle) |
| EDGE-5 | Plan 02 | Satisfied (two-cycle confirmation via orphan_check_count) |
| EDGE-6 | Plan 02 | Satisfied (notifications in writers; _handle_orphan never double-sends) |
| EDGE-8 | Plan 02 | Satisfied (missing footer re-attachment via update_task) |
| SYNC-10 | Plan 01 | Satisfied (reconciler relies on enqueue_sync dedup) |

Phase 7 is complete. All 10 phase requirement IDs satisfied across Plans 01 + 02.

## Threat Model Compliance

| Threat | Mitigation | Status |
|--------|-----------|--------|
| T-7-01: Transient 404 causes accidental deletion | Two-cycle confirmation + rate-limit skip | Implemented |
| T-7-02: Notification flooding | sync_state row deleted after orphan handling | Implemented |
| T-7-07: Owner.id comparison (EDGE-1) | `str(owner_raw)` cast + empty-dict default | Implemented |
| T-7-08: Footer re-attachment overwrites user content | Purely additive `description + suffix` | Implemented |
| T-7-09: Orphan deletion without audit trail | SyncEvent(action='orphan', source='reconciler') | Implemented |
| T-7-10: Task IDs in Resend body | Accepted — opaque IDs, no PII | Accepted |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] arq CronJob second attribute is int not set**
- **Found during:** Task 2 static verification
- **Issue:** Plan's test template asserted `reconcile.second == {0}` but arq 0.28.0 stores `second` as `int 0`, not `set {0}`
- **Fix:** Updated test assertion to `assert reconcile.second in (0, {0})` and `assert orphan.second in (0, {0})` for version-tolerance
- **Files modified:** `tests/unit/test_worker_settings.py`
- **Commit:** included in feat(07) commit

**2. [Rule 2 - Missing Critical Functionality] send_deletion_notification not double-called**
- **Found during:** Task 2 pre-implementation read of writer.py
- **Issue:** Plan's behavior spec said "send_deletion_notification called" as an assertion, but both `delete_todoist_task` and `delete_zoho_task` already call it internally. Adding another call would send 2 emails per orphan.
- **Fix:** `_handle_orphan` does NOT call `send_deletion_notification` directly. Test mocks patch it but do not assert it was called.
- **Files modified:** `app/worker/reconciler.py`, `tests/unit/test_reconciler.py`

## Known Stubs

None — all logic is fully wired.

## Phase 7 Readiness

Phase 7 is complete:
- `reconciler_last_run` (Plan 01) and `orphan_sweep_last_run` (Plan 02) both written to `kv_store`
- Both crons registered in `WorkerSettings.cron_jobs`, picked up at worker startup
- Phase 8 `/health` endpoint can consume both KV timestamps
- Ready for `/gsd-verify-work`

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| `app/worker/reconciler.py` has `orphan_sweep` | FOUND |
| `app/worker/reconciler.py` has `_handle_orphan` | FOUND |
| `app/worker/reconciler.py` has `KV_ORPHAN_SWEEP_LAST_RUN` | FOUND |
| `app/worker/settings.py` has `cron_jobs` | FOUND |
| Commit acd25f2 (RED) exists | FOUND |
| Commit c436749 (GREEN) exists | FOUND |
| 19/19 reconciler tests pass | PASSED |
| 260/260 total tests pass | PASSED |
