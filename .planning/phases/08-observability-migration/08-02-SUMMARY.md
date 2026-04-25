---
phase: 08-observability-migration
plan: "02"
subsystem: worker/cron
tags: [observability, cron, worker, todoist, tdd]
requirements: [OBS-3, OBS-4]

dependency_graph:
  requires:
    - app/db/models.py (SyncEvent model)
    - app/core/config.py (get_settings, todoist_project_id)
    - app/core/logging.py (get_logger)
    - app/worker/settings.py (WorkerSettings.cron_jobs)
  provides:
    - app/worker/daily_summary.py (daily_summary async cron function)
    - daily_summary registered in WorkerSettings.cron_jobs at hour=0, minute=0
  affects:
    - app/worker/settings.py (cron_jobs list extended from 2 to 3 entries)
    - tests/unit/test_worker_settings.py (cron count assertion updated)

tech_stack:
  added: []
  patterns:
    - arq cron function with ctx dict (mirrors reconciler.py pattern)
    - SQLAlchemy async session with delete() + select(func.count()) pattern
    - D-09 ordering: DELETE committed before COUNT queries execute
    - Direct todoist_client._api.add_task() + complete_task() (bypasses update_todoist_task() which strips description)

key_files:
  created:
    - app/worker/daily_summary.py
    - tests/unit/test_daily_summary.py
  modified:
    - app/worker/settings.py
    - tests/unit/test_worker_settings.py

decisions:
  - Used set notation {0} for hour= and minute= in cron() registration for consistency with existing orphan_sweep pattern (minute={0})
  - Two separate session_factory() context managers used: one for DELETE+commit, one for the three COUNT queries — this ensures the 90-day purge is committed before counts are taken (D-09)
  - test_worker_settings.py cron count updated from 2 to 3 — this is an intentional breaking change reflecting the new cron entry

metrics:
  duration: ~15 min
  completed: 2026-04-25T10:27:02Z
  tasks_completed: 2
  files_created: 2
  files_modified: 2
---

# Phase 8 Plan 2: Daily Summary Cron Summary

**One-liner:** Midnight UTC arq cron that purges 90-day-old sync_events then creates+completes a "Sync summary: {date}" Todoist task with live counts.

## What Was Built

### app/worker/daily_summary.py (new, 75 lines)

Async arq cron function `daily_summary(ctx: dict)` implementing:

1. **Step 1 — 90-day cleanup (OBS-4, D-09):** `DELETE FROM sync_events WHERE created_at < now() - 90d`, committed in its own session before any COUNT queries run.
2. **Step 2 — 24h count queries:** Three `SELECT COUNT(*)` queries filtered by `action IN ('sync', 'error', 'echo_suppressed')` and `created_at > now() - 24h` (post-cleanup state).
3. **Step 3 — Task creation (OBS-3, D-08):** `todoist_client._api.add_task(content="Sync summary: {date}", description="{N} syncs, {M} errors, {P} echoes suppressed", project_id=settings.todoist_project_id)`.
4. **Step 4 — Immediate completion (D-07):** `todoist_client._api.complete_task(task.id)` called right after `add_task` returns.
5. **Structured logging:** `log.info("daily_summary_start")` at entry, `log.info("daily_summary_complete", deleted=..., syncs=..., errors=..., echoes=..., todoist_task_id=...)` at exit.

### app/worker/settings.py (modified)

- Added `from app.worker.daily_summary import daily_summary` import.
- Appended `cron(daily_summary, hour={0}, minute={0}, second=0, timeout=120)` to `WorkerSettings.cron_jobs`. List now has 3 entries.

### tests/unit/test_daily_summary.py (new, 8 tests)

TDD RED→GREEN cycle covering OBS-3, OBS-4, D-07, D-08, D-09:

| Test | Covers |
|------|--------|
| test_daily_summary_task_title | Task content is `Sync summary: {YYYY-MM-DD}` (today UTC) |
| test_daily_summary_task_description_format | Description exactly `{N} syncs, {M} errors, {P} echoes suppressed` (D-08) |
| test_daily_summary_uses_project_id | add_task called with correct project_id (OBS-3) |
| test_daily_summary_task_completed | complete_task(task.id) called right after add_task (D-07) |
| test_90day_cleanup_runs | DELETE executed and committed (OBS-4) |
| test_cleanup_before_count | First execute call is DELETE; subsequent calls are not DELETE (D-09) |
| test_count_uses_24h_window | Count query results flow through to task description |
| test_logs_summary_event | log.info("daily_summary_complete", syncs=..., errors=..., echoes=...) emitted |

### tests/unit/test_worker_settings.py (modified)

Updated `test_cron_jobs_registered`: count assertion `len(cron_jobs) == 2` → `== 3`; added assertions for `daily_summary` cron entry (hour, minute, timeout).

## Deviations from Plan

None — plan executed exactly as written. The test_worker_settings.py update was an expected consequence of adding the third cron entry (Rule 1: keeping existing tests accurate, not a deviation).

## Known Stubs

None — all counts are computed from real DB queries; no hardcoded or mock values flow to the Todoist task content.

## Threat Flags

No new network endpoints, auth paths, file access patterns, or schema changes beyond what is documented in the plan's threat model (T-08-05 through T-08-08).

## Test Results

- `pytest tests/unit/test_daily_summary.py -x -q`: 8 passed
- `pytest tests/unit/test_worker_settings.py -x -q`: 13 passed
- `pytest tests/ -q --ignore=tests/unit/test_health.py`: 277 passed, 2 warnings
  - `test_health.py` excluded: pre-existing RED-phase artifact from a parallel wave plan (08-01), confirmed failing before these changes

## Self-Check: PASSED

| Item | Status |
|------|--------|
| app/worker/daily_summary.py | FOUND |
| tests/unit/test_daily_summary.py | FOUND |
| .planning/phases/08-observability-migration/08-02-SUMMARY.md | FOUND |
| commit aa6c18d (RED — failing tests) | FOUND |
| commit 3d654f6 (GREEN — implementation) | FOUND |
