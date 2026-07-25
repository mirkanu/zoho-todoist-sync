---
phase: 09-nirvana-taskprovider
plan: 05
subsystem: sync-pipeline
tags: [taskprovider, todoist, nirvana, fastapi, arq, webhook]

# Dependency graph
requires:
  - phase: 09-nirvana-taskprovider (plan 02)
    provides: "sync_state.external_task_id / provider columns (schema rename)"
  - phase: 09-nirvana-taskprovider (plan 04)
    provides: "TaskProvider Protocol + get_provider() factory in app/providers/base.py"
provides:
  - "app/worker/jobs.py sync_task pipeline calls ctx['task_provider'].fetch/create/update/complete exclusively — no direct Todoist writer imports"
  - "app/webhooks/router.py /todoist route no-ops (200, zero DB writes) when TASK_PROVIDER != todoist (D-13)"
  - "app/main.py boots via get_provider(settings) for either provider value; startup_sync only runs for todoist"
affects: [09-06, 09-07]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Provider-generic direction strings in SyncEvent.detail (zoho_to_external / external_to_zoho) replacing the old zoho_to_todoist / todoist_to_zoho literals going forward"

key-files:
  created: []
  modified:
    - app/worker/jobs.py
    - app/webhooks/router.py
    - app/main.py
    - tests/unit/test_worker_jobs.py
    - tests/unit/test_webhooks.py
    - tests/unit/test_main_lifespan.py
    - tests/conftest.py

key-decisions:
  - "D-13 gate placed AFTER HMAC verification but BEFORE JSON payload parsing, so an unsigned/forged request always gets the same 401 response regardless of which provider is active (no timing/response-shape oracle)."
  - "Fixed a pre-existing conftest.py regression (introduced by an earlier plan in this phase, commit 4b6c80e) where TODOIST_PROJECT_ID was accidentally changed from the real project ID to a placeholder that didn't match 5 existing webhook tests' hardcoded payload project_id — restored to the real value."

requirements-completed: [D-01, D-07, D-13]

duration: ~35min
completed: 2026-07-25
---

# Phase 09 Plan 05: Rewire jobs.py / webhooks / main.py to TaskProvider Summary

**The three call-sites that hardcoded Todoist (`sync_task`'s pipeline, the `/webhooks/todoist` route, and app startup) now go through the provider-agnostic `TaskProvider` interface, making `TASK_PROVIDER` a live config switch for the request-triggered sync path.**

## Performance

- **Duration:** ~35 min
- **Tasks:** 3 completed
- **Files modified:** 7 (3 source, 3 test, 1 shared test-fixture fix)

## Accomplishments
- `app/worker/jobs.py`'s `sync_task`, `_execute_sync`, `_apply_write`, and `_handle_new_task` call `ctx["task_provider"].fetch/create/update/complete` exclusively — no more direct imports of `create_todoist_task`/`update_todoist_task`/`complete_todoist_task`/`todoist_task_to_normalised`.
- `/webhooks/todoist` returns 200 immediately with zero DB writes when a non-Todoist provider is active (D-13), logged as `todoist_webhook_provider_inactive`; `_lookup_zoho_id` now queries the renamed `external_task_id` column.
- `app/main.py` boots via `get_provider(settings)` instead of hardcoding `TodoistClient`; `startup_sync` (Todoist's Sync-API token warm-up) only runs when Todoist is the active provider, per D-09.

## Task Commits

1. **Task 1: Rewire app/worker/jobs.py to the TaskProvider interface** - `aafec05` (refactor)
2. **Task 2: Provider-aware /webhooks/todoist route (D-13) + external_task_id rename** - `1f18c75` (feat)
3. **Task 3: Wire app/main.py through get_provider()** - `361bff4` (refactor)

## Files Created/Modified
- `app/worker/jobs.py` - sync_task pipeline rewired to `ctx["task_provider"]`; direction strings now `zoho_to_external`/`external_to_zoho`; `_handle_new_task` writes `provider=get_settings().task_provider` on new `SyncState` rows.
- `app/webhooks/router.py` - `_lookup_zoho_id` queries `SyncState.external_task_id`; D-13 gate added after HMAC verification.
- `app/main.py` - `get_provider(settings)` replaces `TodoistClient(...)`; `app.state.task_provider` replaces `app.state.todoist_client`; `startup_sync` gated on `settings.task_provider == "todoist"`.
- `tests/unit/test_worker_jobs.py` - full mocking-pattern rewrite: `ctx["task_provider"]` AsyncMock methods replace patches of module-level Todoist writer functions; all 17 tests pass under the new pattern.
- `tests/unit/test_webhooks.py` - `_lookup_zoho_id` test payloads unaffected (helper mock already used generic `todoist_task_id` param name); added `test_todoist_webhook_noop_when_provider_inactive` for D-13; lifespan test patches `app.main.get_provider` instead of `app.main.TodoistClient`.
- `tests/unit/test_main_lifespan.py` - lifespan tests patch `app.main.get_provider`; added `test_lifespan_skips_startup_sync_when_nirvana_active` for D-09.
- `tests/conftest.py` - fixed `TODOIST_PROJECT_ID` placeholder regression (see Deviations).

## Decisions Made
- Kept the D-13 gate positioned after HMAC verification (a security decision already locked in the plan/threat model) rather than before, so forged requests can't be used to probe which provider is currently active via response-shape differences.
- Added one new test per Task 2 and Task 3 beyond what the plan's acceptance criteria strictly required (`test_todoist_webhook_noop_when_provider_inactive`, `test_lifespan_skips_startup_sync_when_nirvana_active`) since the plan's `<done>` criteria describe behavior (D-13 no-op, D-09 skip) that wasn't otherwise directly exercised by a dedicated test.

## Deviations from Plan

### Auto-fixed Issues

**1. Pre-existing test fixture regression blocking verification**
- **Found during:** Task 2 (running `pytest tests/unit/test_webhooks.py`)
- **Issue:** `tests/conftest.py`'s `TODOIST_PROJECT_ID` was `"test-project-id"`, a value that doesn't match the real project ID (`"6gCPcWwM392GhXQh"`) hardcoded in 5 existing webhook tests' payloads, causing those tests to hit the "wrong project" discard path and fail with "0 enqueue calls" instead of 1. Traced via `git log -p` to commit `4b6c80e` (an earlier plan in this same phase, adding `TASK_PROVIDER`/`NIRVANA_PAT` settings), which accidentally changed this value from the correct `"6gCPcWwM392GhXQh"` to the placeholder while touching an unrelated part of the file.
- **Fix:** Restored `TODOIST_PROJECT_ID` to `"6gCPcWwM392GhXQh"` in `tests/conftest.py`.
- **Files modified:** `tests/conftest.py`
- **Verification:** `pytest tests/unit/test_webhooks.py -q` — 24/24 passed before adding the new D-13 test, 25/25 after.
- **Committed in:** `1f18c75` (part of Task 2 commit)

---

**Total deviations:** 1 auto-fixed (pre-existing shared-fixture bug, not scope creep — it was blocking this plan's own acceptance criterion of `pytest tests/unit/test_webhooks.py -x` exiting 0).
**Impact on plan:** No scope creep. All other work matches the plan exactly.

## Issues Encountered
While running the full `tests/unit` suite (not required by this plan's acceptance criteria, done as an extra sanity check), found a separate pre-existing test-isolation flake: `tests/unit/test_notifications.py::test_sender_overridden_by_env_var` fails only when run as part of a sufficiently large combination of other test files (reproducible on the unmodified base commit `83c27be` too, confirmed via a scratch clone). Root cause not fully isolated (likely cumulative `os.environ`/`lru_cache` state bleed across test modules, unrelated to `sync_state`/`TaskProvider`). Out of scope for this plan (not one of the three files this plan owns, and not in this plan's required verification command) — left as-is, not fixed. `tests/unit/test_reconciler.py`'s 2 remaining failures are Plan 09-06's responsibility per this plan's dispatch instructions, confirmed still `external_task_id`-rename-shaped failures in `app/worker/reconciler.py` (untouched by this plan).

## User Setup Required
None — no external service configuration required.

## Next Phase Readiness
`app/worker/jobs.py`, `app/webhooks/router.py`, and `app/main.py` are fully migrated off Todoist-specific hardcoding for the request-triggered sync path. Plan 09-06 (parallel, `app/worker/reconciler.py`) completes the cron-triggered path. Once both land, Plan 09-07 can wire `WorkerSettings.on_startup` to populate `ctx["task_provider"]` via `get_provider()` for the arq worker process (jobs.py already expects that key to exist in `ctx`).

---
*Phase: 09-nirvana-taskprovider*
*Completed: 2026-07-25*
