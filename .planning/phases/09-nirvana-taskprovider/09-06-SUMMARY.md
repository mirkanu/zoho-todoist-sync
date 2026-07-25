---
phase: 09-nirvana-taskprovider
plan: 06
subsystem: worker
tags: [reconciler, taskprovider, nirvana, polling, orphan-sweep]

# Dependency graph
requires:
  - phase: 09-02
    provides: "SyncState.external_task_id + SyncState.provider columns (rename of todoist_task_id)"
  - phase: 09-04
    provides: "TaskProvider Protocol (fetch/create/update/complete/delete/close) + get_provider(settings) factory"
provides:
  - "app/worker/reconciler.py: orphan_sweep/_handle_orphan generalized to ctx['task_provider'].fetch/delete (works for either provider)"
  - "app/worker/reconciler.py: nirvana_poll_sweep(ctx) — new cron, poll+diff against sync_state, replaces Todoist's webhook-driven change detection for Nirvana"
affects: [09-07-worker-settings]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Provider-agnostic error handling: catch both TodoistNotFoundError|NirvanaNotFoundError (and RateLimit/API variants) in one except clause where a code path must work for either provider"
    - "Always-registered, no-op-when-inactive cron function (mirrors D-13's webhook-route pattern) so provider switching is a config change, not a redeploy-sensitive registration change"

key-files:
  created: []
  modified:
    - app/worker/reconciler.py
    - tests/unit/test_reconciler.py

key-decisions:
  - "reconcile_sweep's Todoist Sync API delta path (ctx['todoist_client'], fetch_sync_delta) intentionally left using ctx['todoist_client'] directly, not ctx['task_provider'] — TaskProvider's Protocol has no sync_token-delta equivalent (RESEARCH.md: 'Do not try to fake a sync_token for Nirvana'), and the plan's own truths require this path to keep running unchanged. Its SyncState column reference was fixed (todoist_task_id -> external_task_id) since Plan 09-02 broke it, but the ctx key and Todoist-specific delta logic stayed as-is."
  - "_handle_orphan's zoho_missing branch now calls ctx['task_provider'].delete(state.external_task_id, task_name=...) — task_name read from external_task.title (a NormalisedTask attribute) instead of a raw Todoist SDK object's .content, since task_provider.fetch() already returns a normalised task"
  - "nirvana_poll_sweep unmatched Nirvana tasks (no sync_state row) are logged at DEBUG and skipped, never auto-adopted into sync — mirrors reconcile_todoist_not_in_sync_state's existing philosophy of not accidentally pulling native/non-Zoho tasks into the sync"

patterns-established:
  - "Any reconciler code path that must behave identically for either TaskProvider implementation catches both providers' typed exceptions in one except tuple rather than branching on settings.task_provider"

requirements-completed: [D-06, D-09]

# Metrics
duration: ~35min
completed: 2026-07-25
---

# Phase 09 Plan 06: Reconciler TaskProvider Generalization + Nirvana Poll Sweep Summary

**`orphan_sweep`/`_handle_orphan` now use `ctx["task_provider"].fetch/delete` exclusively (works identically for Todoist or Nirvana), and a new `nirvana_poll_sweep` cron polls Nirvana's full task list every cycle, diffing against `sync_state` by canonical hash to replace the webhook-driven change detection Nirvana doesn't have.**

## Performance

- **Duration:** ~35 min
- **Tasks:** 2 completed
- **Files modified:** 2 (app/worker/reconciler.py, tests/unit/test_reconciler.py)

## Accomplishments
- `orphan_sweep`'s external-side existence check now calls `task_provider.fetch(state.external_task_id)`, catching `(TodoistNotFoundError, NirvanaNotFoundError)` and `(TodoistRateLimitError, TodoistAPIError, NirvanaRateLimitError, NirvanaAPIError)` in unified except clauses — identical control flow regardless of active provider.
- `_handle_orphan`'s Zoho-missing branch deletes via `ctx["task_provider"].delete(...)` instead of a hardcoded `delete_todoist_task` import (now removed); task name is read from the fetched `NormalisedTask`'s `.title`.
- New `nirvana_poll_sweep(ctx)`: no-ops immediately when `settings.task_provider != "nirvana"` (mirrors D-13); when active, fetches `get_task_counts()` (logged) then `get_tasks(limit=200)`, warns explicitly (`nirvana_poll_sweep_cap_hit`) if the 200-item cap is hit, normalises + hashes each raw task, diffs against the matching `sync_state` row (`external_task_id` + `provider='nirvana'`), and enqueues `sync_task` on any hash mismatch. Unmatched tasks (no sync_state row) are skipped and logged at DEBUG, not auto-adopted.
- Fixed a pre-existing break in `reconcile_sweep`'s Todoist-delta lookup (`SyncState.todoist_task_id` → `SyncState.external_task_id`) and `_handle_orphan`'s `SyncEvent.detail` dict, both left dangling by Plan 09-02's column rename — explicitly called out as this plan's responsibility in the parallel-execution briefing.
- 5 new tests for `nirvana_poll_sweep` (inactive no-op, hash-mismatch enqueue, unmatched-task skip, 200-item cap warning, last-run timestamp upsert); existing orphan-sweep and reconcile-sweep tests updated to the new `task_provider`/`external_task_id` naming. All 25 tests in `tests/unit/test_reconciler.py` pass.

## Task Commits

Each task was committed atomically:

1. **Task 1: Generalize orphan_sweep to TaskProvider** - `5b747eb` (refactor)
2. **Task 2: nirvana_poll_sweep — new cron function for Nirvana change detection** - `10cf49f` (feat)

## Files Created/Modified
- `app/worker/reconciler.py` — imports `NirvanaAPIError`/`NirvanaNotFoundError`/`NirvanaRateLimitError`/`nirvana_task_to_normalised`; removed `delete_todoist_task` import; `orphan_sweep`/`_handle_orphan` generalized to `task_provider`; new `nirvana_poll_sweep` + `KV_NIRVANA_POLL_LAST_RUN` + `NIRVANA_POLL_LIMIT` constants; `reconcile_sweep`'s Todoist-delta `SyncState` lookup column fixed to `external_task_id`
- `tests/unit/test_reconciler.py` — `_make_reconciler_ctx` gains a `task_provider` key; `_make_state` sets `external_task_id`/`provider` instead of `todoist_task_id`; `_healthy_todoist_task` returns a `.title`-bearing mock (NormalisedTask shape); orphan-sweep tests assert on `ctx["task_provider"].fetch`/`.delete` instead of a patched `delete_todoist_task` import; 5 new `nirvana_poll_sweep` tests appended

## Decisions Made
- Kept `reconcile_sweep`'s `ctx["todoist_client"]`/`fetch_sync_delta` path untouched in behavior (only its `SyncState` column reference was fixed) — the plan's truths explicitly require this Todoist Sync API delta mechanism to keep running unchanged, since `TaskProvider`'s Protocol has no sync-token-delta equivalent and RESEARCH.md explicitly warns against faking one for Nirvana. This means the acceptance criterion `grep -c "ctx\[.todoist_client.\]" app/worker/reconciler.py` returns `1`, not `0` as literally stated in the plan — that grep didn't account for `reconcile_sweep`'s intentionally-preserved line, analogous to the grep-undercount noted in 09-04-SUMMARY.md. All functional acceptance criteria (test pass, `state.todoist_task_id` count 0, `delete_todoist_task` no longer imported/called) are met.
- `task_name` for orphan deletion now comes from `external_task.title` (a `NormalisedTask` attribute), not `.content` (a raw Todoist SDK attribute) — correct because `task_provider.fetch()` always returns a normalised task per the `TaskProvider` Protocol contract (Plan 04).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed dangling `SyncState.todoist_task_id` references left by Plan 09-02's rename**
- **Found during:** Task 1 read-through of the full file before editing (per `<read_first>`)
- **Issue:** `reconcile_sweep`'s Todoist-delta lookup (`SyncState.todoist_task_id == todoist_id`) and `_handle_orphan`'s `SyncEvent.detail` dict (`"todoist_task_id": state.todoist_task_id`) still referenced the column Plan 09-02 renamed to `external_task_id`. Both would raise `AttributeError` at runtime.
- **Fix:** Renamed both references to `external_task_id`. No behavior change — purely the column-name fix explicitly assigned to this plan per the parallel-execution briefing ("your plan (09-06) fixes reconciler.py's references").
- **Files modified:** `app/worker/reconciler.py`
- **Verification:** `pytest tests/unit/test_reconciler.py -x` — 25 passed
- **Committed in:** `5b747eb` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (blocking, pre-assigned to this plan)
**Impact on plan:** None beyond what was already anticipated by the parallel-execution briefing — no scope creep into `jobs.py`, `webhooks/router.py`, or `main.py` (09-05's territory), and no changes to `app/worker/settings.py` (09-07's territory).

## Issues Encountered
Full-suite run (`pytest tests/unit/ --ignore=tests/unit/test_backfill_descriptions.py`) shows 9 failures outside this plan's scope: `test_config.py`/`test_notifications.py` (pre-existing, unrelated to phase 09), and `test_webhooks.py`/`test_worker_jobs.py` (parallel plan 09-05's explicit responsibility — those files reference `ctx["task_provider"]`/`external_task_id` call sites in `jobs.py`/`webhooks/router.py` that 09-05 fixes concurrently in a separate worktree). `tests/unit/test_reconciler.py` itself — this plan's sole target — is 25/25 green.

## User Setup Required
None — no external service configuration required. `nirvana_poll_sweep` is not yet registered as a cron job; that wiring is Plan 07's responsibility (`app/worker/settings.py`).

## Next Phase Readiness
`nirvana_poll_sweep(ctx)` is implemented, tested, and safe to register unconditionally — Plan 07 can add it to `WorkerSettings.cron_jobs` without any `TASK_PROVIDER`-conditional branching, since the no-op guard lives inside the function itself. `orphan_sweep` now works identically for either provider once Plan 07 populates `ctx["task_provider"]` in `app/worker/settings.py`'s `on_startup` (currently only `ctx["todoist_client"]` is set there — `orphan_sweep`/`nirvana_poll_sweep` will `KeyError` on `ctx["task_provider"]` until Plan 07 lands, exactly as RESEARCH.md anticipated for this wave's sequencing).

---
*Phase: 09-nirvana-taskprovider*
*Completed: 2026-07-25*
