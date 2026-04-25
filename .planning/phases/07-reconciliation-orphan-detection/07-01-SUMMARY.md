---
phase: 07-reconciliation-orphan-detection
plan: "01"
subsystem: worker-reconciler
tags: [reconciliation, cron, seed-5, seed-7, tdd]
dependency_graph:
  requires:
    - app/worker/enqueue.py          # enqueue_sync
    - app/todoist/sync_manager.py    # load_sync_token, save_sync_token
    - app/zoho/token_manager.py      # upsert_kv
    - app/zoho/normalise.py          # zoho_record_to_normalised
    - app/todoist/normalise.py       # extract_zoho_id
    - app/core/hash.py               # canonical_hash
    - app/db/models.py               # SyncState
  provides:
    - app/worker/reconciler.py       # reconcile_sweep, KV_RECONCILER_LAST_RUN, RECONCILE_LOOKBACK_MINUTES
  affects:
    - app/worker/settings.py         # Plan 02 will import reconcile_sweep for cron_jobs
tech_stack:
  added: []
  patterns:
    - arq cron function (ctx dict, no job_try)
    - session_factory async context manager (one session per logical operation)
    - upsert_kv caller-commits pattern (upsert_kv does NOT auto-commit)
    - save_sync_token internally commits (confirmed in sync_manager.py line 45)
key_files:
  created:
    - app/worker/reconciler.py       # 98 lines
    - tests/unit/test_reconciler.py  # 402 lines
  modified: []
decisions:
  - "save_sync_token commits internally (calls session.commit() itself at line 45 of sync_manager.py); reconciler opens a fresh session and calls save_sync_token which handles commit — no extra commit needed for token persistence"
  - "Todoist footer IDs must be digits-only per ZOHO_ID_RE regex (\\d+); test data corrected from [zoho:Z100] to [zoho:100]"
  - "ZohoAPIError not swallowed — arq cron framework logs/timeouts propagated exceptions; reconciler stays simple"
metrics:
  duration_minutes: 10
  completed_date: "2026-04-25"
  tasks_completed: 2
  files_created: 2
  files_modified: 0
  tests_added: 10
  tests_total_after: 250
---

# Phase 7 Plan 01: reconcile_sweep Cron Function Summary

**One-liner:** reconcile_sweep cron fetches Zoho 20-min modified-since window and Todoist incremental delta, enqueues sync_task on hash mismatch, persists sync_token and last_run timestamp.

## Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `app/worker/reconciler.py` | 98 | reconcile_sweep cron + KV constants |
| `tests/unit/test_reconciler.py` | 402 | 10 unit tests (TDD RED then GREEN) |

## Test Results

- `python3 -m pytest tests/unit/test_reconciler.py -x -q` → **10 passed**
- `python3 -m pytest tests/ -x -q` → **250 passed, 2 warnings** (no regressions)

## Implementation Notes

### reconcile_sweep Algorithm

1. Pull `redis`, `session_factory`, `zoho_client`, `todoist_client` from ctx.
2. `settings = get_settings()`.
3. `since = datetime.now(timezone.utc) - timedelta(minutes=20)`.
4. **Zoho side:** `fetch_tasks_modified_since(since, settings.zoho_user_id)` → per record: compute `canonical_hash(zoho_record_to_normalised(record, terminal_statuses_list))` → SELECT SyncState → enqueue if state is None or hash mismatch.
5. **Todoist side:** load sync_token → `fetch_sync_delta(sync_token=stored, project_id=...)` → save new_token (BEFORE processing, crash-safe) → per item: skip is_deleted, skip no-footer, enqueue valid items.
6. **Last-run:** `upsert_kv(session, "reconciler_last_run", now.isoformat())` then `session.commit()`.

### Notable Finding: save_sync_token commit semantics

`save_sync_token` (sync_manager.py line 38-45) calls `await session.commit()` internally. This means the reconciler opens a fresh session, passes it to `save_sync_token`, and the function handles the commit. No extra commit needed for token persistence.

### Test Data Bug Fixed (Rule 1)

Test 4 (`test_reconcile_todoist_delta`) and test 6 (`test_reconcile_todoist_delta_is_deleted_skipped`) originally used `[zoho:Z100]` and `[zoho:Z999]` as footer IDs. The `ZOHO_ID_RE` regex matches `\[zoho:(\d+)\]` (digits only), so `Z100` returns `None`. Fixed to `[zoho:100]` and `[zoho:999]` respectively.

## Threat Model Compliance

| Threat | Mitigation | Status |
|--------|-----------|--------|
| T-7-03: Tampering via user-controlled Todoist description | `extract_zoho_id` strict digits-only regex; non-matching descriptions silently skipped | Implemented |
| T-7-04: DoS via excessive enqueue | `enqueue_sync` dedup via `_job_id=f"sync:{zoho_task_id}"` (SYNC-10) — reconciler relies on this entirely | Implemented |
| T-7-05: Repudiation of sweep state | sync_token persisted BEFORE processing; last_run upserted AFTER complete sweep | Implemented |
| T-7-06: Info disclosure in logs | Only `todoist_id` logged on no-footer skip; description body never logged | Implemented |

## Setup for Plan 02

`reconcile_sweep` is fully importable:
```python
from app.worker.reconciler import reconcile_sweep, KV_RECONCILER_LAST_RUN, RECONCILE_LOOKBACK_MINUTES
```
Plan 02 will add `orphan_sweep` to the same module and register both in `WorkerSettings.cron_jobs`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed non-digit Zoho IDs in test 4 and test 6**
- **Found during:** Task 2 first test run
- **Issue:** Test data used `[zoho:Z100]`, `[zoho:Z200]`, `[zoho:Z999]` but `ZOHO_ID_RE` requires `\d+` (digits only), so `extract_zoho_id` returned None for all, causing test 4 to fail (0 enqueue calls instead of 2) and test 6 to trivially pass for wrong reason
- **Fix:** Changed test IDs to `[zoho:100]`, `[zoho:200]`, `[zoho:999]`; assertions updated to expect `"100"` and `"200"` as extracted IDs
- **Files modified:** `tests/unit/test_reconciler.py`
- **Commit:** 7b1c8aa (included in GREEN commit)

## Known Stubs

None — all logic is fully wired.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| `app/worker/reconciler.py` exists | FOUND |
| `tests/unit/test_reconciler.py` exists | FOUND |
| `07-01-SUMMARY.md` exists | FOUND |
| Commit 920c578 (RED) exists | FOUND |
| Commit 7b1c8aa (GREEN) exists | FOUND |
| 10/10 tests pass | PASSED |
| 250 total tests pass (no regressions) | PASSED |
