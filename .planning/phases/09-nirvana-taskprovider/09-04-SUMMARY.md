---
phase: 09-nirvana-taskprovider
plan: 04
subsystem: infra
tags: [protocol, factory-pattern, todoist, nirvana, typing]

# Dependency graph
requires:
  - phase: 09-01
    provides: "Settings.task_provider / nirvana_pat / todoist_api_token fields"
  - phase: 09-03
    provides: "NirvanaClient (fetch/create/update/complete/delete/close) — the Protocol surface TodoistClient must match"
provides:
  - "app/providers/base.py: TaskProvider typing.Protocol (6-method surface) + get_provider(settings) factory"
  - "TodoistClient now exposes fetch/create/update/complete/delete/close, satisfying TaskProvider structurally"
affects: [worker-jobs, reconciler, webhooks-router, main-lifespan, worker-settings]

# Tech tracking
tech-stack:
  added: []
  patterns: ["typing.Protocol for structural interfaces (no ABC inheritance rewrite)", "factory function reading Settings to select concrete implementation"]

key-files:
  created:
    - app/providers/__init__.py
    - app/providers/base.py
    - tests/unit/test_provider_factory.py
  modified:
    - app/todoist/client.py
    - tests/unit/test_todoist_client.py

key-decisions:
  - "TaskProvider defined as typing.Protocol per RESEARCH.md recommendation — TodoistClient has no base class today, so structural typing avoids an inheritance rewrite of shipped code."
  - "get_provider() does deferred (function-local) imports of TodoistClient/NirvanaClient to avoid import-time coupling between app/providers and both concrete client modules."

patterns-established:
  - "Thin delegation pattern: new Protocol-conformance methods on TodoistClient call straight through to existing app/todoist/writer.py functions unchanged — zero rewrite of shipped, running-since-2026-05-01 internals (Pitfall 3)."

requirements-completed: [D-01]

# Metrics
duration: ~25min
completed: 2026-07-25
---

# Phase 09 Plan 04: TaskProvider Protocol + Factory Summary

**TaskProvider `typing.Protocol` (fetch/create/update/complete/delete/close) plus a `get_provider(settings)` factory dispatching on `TASK_PROVIDER`; TodoistClient extended with 5 thin delegation methods to conform, with zero changes to existing writer.py internals.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 completed
- **Files modified:** 5 (3 created, 2 modified)

## Accomplishments
- `app/providers/base.py` declares the `TaskProvider` Protocol (6 async methods) and `get_provider(settings)` — returns `TodoistClient` for `task_provider == "todoist"`, `NirvanaClient` for `"nirvana"`, raises `ValueError` (message includes the bad value) for anything else.
- `TodoistClient` gained `fetch`/`create`/`update`/`complete`/`delete` methods that delegate to `app/todoist/normalise.py` and `app/todoist/writer.py` unchanged — `close()` already existed. `create()` still forwards an optional `description` kwarg unchanged, preserving the existing DESC-1..4 Zoho-link/deal-context write behavior.
- 21 new unit tests (3 in `test_provider_factory.py`, 18 total including pre-existing in `test_todoist_client.py`, of which 6 are new for this plan) — all passing.

## Task Commits

Each task was committed atomically:

1. **Task 1: TaskProvider Protocol + get_provider() factory** - `be3e944` (feat)
2. **Task 2: TodoistClient Protocol conformance (thin delegation methods)** - `8204df3` (feat)

## Files Created/Modified
- `app/providers/__init__.py` - empty package marker
- `app/providers/base.py` - `TaskProvider` Protocol + `get_provider(settings)` factory
- `tests/unit/test_provider_factory.py` - 3 tests covering todoist/nirvana dispatch + unknown-value ValueError
- `app/todoist/client.py` - added `fetch`/`create`/`update`/`complete`/`delete` delegation methods + top-level `NormalisedTask` import; existing `fetch_todoist_task`/`fetch_sync_delta`/`close` untouched
- `tests/unit/test_todoist_client.py` - added 6 tests for the new delegation methods (fetch, create x2 with/without description, update, complete, delete)

## Decisions Made
- Followed the plan's prescribed code verbatim for both `app/providers/base.py` and the `TodoistClient` additions — no design decisions beyond the plan's `<action>` blocks were needed.
- Used `Settings.model_construct(...)` (not env-var + `Settings()`) in `test_provider_factory.py` per the plan's own suggested simpler approach, since these are plain attribute reads with no validation/network calls involved.

## Deviations from Plan

None — plan executed exactly as written. One documentation-only discrepancy noted below (not a deviation in delivered code).

### Note: plan's own acceptance-criteria grep undercounts by 2

The plan's Task 2 acceptance criterion `grep -c "async def fetch\|async def create\|async def update\|async def complete\|async def delete" app/todoist/client.py returns 5` does not account for the pre-existing `fetch_todoist_task` and `fetch_sync_delta` methods, whose names also match the `async def fetch` substring pattern. Actual count is 7 (2 pre-existing + 5 new), which is correct and expected — verified by listing all matching lines. All other acceptance criteria (existing-methods count = 2, `description=description` count = 1, both pytest invocations) pass exactly as specified. No code change was needed; this is purely an imprecise grep pattern in the plan document itself.

## Issues Encountered

Full test suite run (`pytest --ignore=tests/unit/test_backfill_descriptions.py`) shows 11 pre-existing failures (`test_config.py`, `test_notifications.py`, `test_reconciler.py`, `test_webhooks.py`, `test_worker_jobs.py`) unrelated to this plan's changes — confirmed identical failure set and count before and after this plan's commits via `git stash`/re-run comparison. Root cause appears to be other in-flight wave-3 work (e.g. the `sync_state.external_task_id`/`provider` column rename, D-12) not yet present in this worktree. Not addressed here — out of this plan's scope, and this plan's own new tests (21) all pass cleanly.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
`get_provider(settings)` is ready for wiring into `app/main.py` lifespan, `app/worker/settings.py` on_startup, and any worker job/reconciler code that currently constructs `TodoistClient` directly — those call sites can now switch to `get_provider(settings)` and gain Nirvana support via a config change (D-01 fully closed for the client layer). The pre-existing 11 test failures noted above should be resolved by whichever wave-3 plan lands the `sync_state` schema rename (D-12) and its call-site updates, before those provider-selection wiring changes land.

---
*Phase: 09-nirvana-taskprovider*
*Completed: 2026-07-25*
