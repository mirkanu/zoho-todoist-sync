---
phase: 09-nirvana-taskprovider
plan: 07
subsystem: worker
tags: [worker, taskprovider, nirvana, cron, live-verification]

# Dependency graph
requires:
  - phase: 09-04
    provides: "TaskProvider Protocol (fetch/create/update/complete/delete/close) + get_provider(settings) factory"
  - phase: 09-06
    provides: "app/worker/reconciler.py: nirvana_poll_sweep(ctx) — no-ops when settings.task_provider != 'nirvana'"
provides:
  - "app/worker/settings.py: on_startup/on_shutdown populate/close ctx['task_provider'] via get_provider(settings) — last hardcoded-Todoist call site in the worker closed"
  - "app/worker/settings.py: WorkerSettings.cron_jobs registers nirvana_poll_sweep (5th entry, 15-min cadence, always-on/no-op-when-inactive)"
  - "app/nirvana/writer.py: create_tasks result shape and update_tasks duedate-clearing behavior confirmed against the live Nirvana API, docstrings updated to reflect verified (not assumed) behavior"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Live-verification-before-finalizing: rather than shipping defensive/guessed behavior indefinitely, ran one throwaway script against the real Nirvana API, observed the actual result shape, and updated code + docstrings to match confirmed behavior instead of a documented assumption"

key-files:
  created: []
  modified:
    - app/worker/settings.py
    - tests/unit/test_worker_settings.py
    - app/nirvana/writer.py
    - tests/unit/test_nirvana_writer.py

key-decisions:
  - "duedate: '' confirmed live to clear Nirvana's due date (field disappears from a follow-up get_tasks read, versus the prior real date value) — update_nirvana_task now sends duedate: '' when normalised.due_date is None, replacing the previous conservative omit-the-key behavior from Plan 09-03."
  - "create_tasks' real result shape ({\"ok\": true, \"tasks\": [...], \"count\": N}) matches the existing dict-with-\"tasks\"-key branch in _extract_created_items — no code change needed there, only docstring updated from 'unverified' to 'confirmed live'."
  - "nirvana_poll_sweep registered on the same 15-min cadence as reconcile_sweep (minute={0,15,30,45}) — comfortably covers D-09's hourly staleness budget, and the function itself no-ops instantly when Nirvana is inactive so the extra cron tick costs nothing when Todoist is the active provider."

requirements-completed: [D-01, D-04, D-09]

# Metrics
duration: ~25min
completed: 2026-07-28
---

# Phase 09 Plan 07: Worker Settings TaskProvider Wiring + Live Nirvana Verification Summary

**The arq worker's `on_startup`/`on_shutdown`/`cron_jobs` now go through `get_provider(settings)` exclusively (no `TodoistClient` import remains in `app/worker/settings.py`), `nirvana_poll_sweep` is registered as a 5th always-on cron job, and `app/nirvana/writer.py`'s two previously-unverified assumptions (create_tasks result shape, update_tasks duedate-clearing) are now confirmed against a real live Nirvana API call.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 completed
- **Files modified:** 4 (app/worker/settings.py, tests/unit/test_worker_settings.py, app/nirvana/writer.py, tests/unit/test_nirvana_writer.py)

## Accomplishments
- `app/worker/settings.py`'s `on_startup` now sets `ctx["task_provider"] = get_provider(settings)` instead of hardcoding `TodoistClient(...)`; `on_shutdown` closes it generically via `ctx.get("task_provider").close()`. This was the last hardcoded-Todoist call site flagged in RESEARCH.md — the worker no longer crashes with `KeyError` on `ctx["task_provider"]` for jobs/cron functions that already expected it (Plans 05/06).
- `WorkerSettings.cron_jobs` gained a 5th entry: `cron(nirvana_poll_sweep, minute={0, 15, 30, 45}, second=0, timeout=300)` — always registered per the D-13 no-op-when-inactive pattern; switching `TASK_PROVIDER` is a config change, not a redeploy-sensitive registration change.
- All 5 affected tests in `tests/unit/test_worker_settings.py` (startup ctx population, startup refresh-loop launch, shutdown client-close x2, cron_jobs count/names/schedule) updated to the new `task_provider`/`get_provider` naming; all 13 tests pass.
- Live-verified `app/nirvana/writer.py`'s two previously-defensive assumptions using a throwaway script against the real Nirvana API (via the project's own `NirvanaClient`, PAT loaded from `/home/services/.env.production`):
  - `create_tasks([...])` returns `{"ok": true, "tasks": [{"id": ..., ...}], "count": 1}` — matches the existing `"tasks"`-key branch in `_extract_created_items`; no code change, only docstring updated from "unverified" to "confirmed live in Plan 09-07".
  - `update_tasks([{"id": ..., "duedate": ""}])` **does** clear the due date — confirmed by a follow-up `get_tasks` read showing the `duedate` field absent (versus the prior `"2026-08-01"` value). `update_nirvana_task` now sends `duedate: ""` when `normalised.due_date is None`, replacing the previous conservative "omit the key" behavior.
  - Test task `ZTS-PLAN-09-07-VERIFY` was created, mutated, and moved to `state: "trash"` as cleanup — confirmed absent from an active-state (`someday`) scan and present in the trash scan. No live data left behind.
  - Throwaway verification script deleted after use (never committed).

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire on_startup/on_shutdown through get_provider() and register nirvana_poll_sweep** - `f66fec7` (feat)
2. **Task 2: Live-verify Nirvana create_tasks result shape and duedate-clearing behavior; finalize writer.py** - `d3b79b9` (feat)

## Files Created/Modified
- `app/worker/settings.py` — `TodoistClient` import replaced with `get_provider`; `nirvana_poll_sweep` added to the reconciler import; `on_startup`/`on_shutdown` use `ctx["task_provider"]`; `cron_jobs` gains `nirvana_poll_sweep` entry; docstring step 5 updated
- `tests/unit/test_worker_settings.py` — patches/assertions updated from `TodoistClient`/`todoist_client` to `get_provider`/`task_provider`; `test_cron_jobs_registered` updated for 5 entries including `nirvana_poll_sweep`'s schedule
- `app/nirvana/writer.py` — `create_nirvana_task` and `update_nirvana_task` docstrings updated to state live-verified outcomes; `update_nirvana_task` now sends `duedate: ""` instead of omitting the key when `due_date is None`
- `tests/unit/test_nirvana_writer.py` — `test_update_nirvana_task_omits_duedate_when_none` renamed to `test_update_nirvana_task_clears_duedate_when_none`, asserting `updates[0]["duedate"] == ""` instead of key absence

## Decisions Made
- Sent `duedate: ""` (empty string) rather than JSON `null` for the live-clearing test, per the plan's own reasoning: the client's `call_tool` signature takes a Python dict serialized via httpx's `json=` kwarg, and `None` would either be dropped or sent as `null` depending on how the dict is built — empty string is the more common REST clearing convention and was confirmed to work on the first attempt, so no `null` experiment was needed.
- Left `_extract_created_items`'s other defensive branches (bare list, `"created"`/`"items"` keys, single dict with `"id"`) in place as a safety net even though the live call matched the `"tasks"`-key branch — per the plan's explicit instruction to only change the function if the live shape didn't match any existing branch.

## Deviations from Plan
None. Both tasks executed exactly as specified; no blocking issues encountered.

## Issues Encountered
None. The prior session's uncommitted Task 1 diff (interrupted mid-plan by an API session-limit error) was verified against the plan's acceptance criteria and committed as-is without modification — it already matched the plan's action steps exactly.

## User Setup Required
None — no external service configuration required. `NIRVANA_PAT` was already present in `/home/services/.env.production` from prior phase work.

## Next Phase Readiness
`app/worker/settings.py` now boots either `TASK_PROVIDER` value cleanly via `get_provider(settings)`, closing the last hardcoded-Todoist call site in the codebase (per RESEARCH.md). `nirvana_poll_sweep` is scheduled every 15 minutes, always registered and no-op unless Nirvana is active. `app/nirvana/writer.py`'s two previously-deferred assumptions are now resolved with live evidence rather than guesses — Phase 09's Nirvana `TaskProvider` implementation has no known unverified write-path behavior remaining.

---
*Phase: 09-nirvana-taskprovider*
*Completed: 2026-07-28*
