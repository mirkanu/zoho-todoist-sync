---
phase: 09-nirvana-taskprovider
plan: 01
subsystem: infra
tags: [pydantic-settings, config, priority-mapping, docker-compose]

requires: []
provides:
  - "Settings.task_provider / nirvana_pat / nirvana_poll_interval_secs fields"
  - "app/core/priority.py two-axis Todoist-int <-> Nirvana (state, starred) mapping"
  - "Production docker-compose.yml + .env.production wiring for TASK_PROVIDER/NIRVANA_PAT"
affects: [09-nirvana-taskprovider (plans 02-07)]

tech-stack:
  added: []
  patterns:
    - "Two-axis priority mapping (state + starred) mirrors existing ZOHO_TO_TODOIST dict pattern"
    - "Total/defensive mapping functions never raise on unknown input (open-vocabulary state handling)"

key-files:
  created: []
  modified:
    - app/core/config.py
    - app/core/priority.py
    - tests/conftest.py
    - tests/unit/test_config.py
    - tests/unit/test_priority.py
    - /home/services/hetzner-vps/docker-compose.yml (external, not part of this repo)
    - /home/services/.env.production (external, not part of this repo)

key-decisions:
  - "nirvana_pat has no default (required, fails fast) mirroring todoist_api_token; task_provider defaults to 'todoist' so unset env never breaks v1.0 deployment"
  - "todoist_priority_to_nirvana/nirvana_to_todoist_priority key off canonical Todoist-int, not raw Zoho string, since NormalisedTask.priority is already converted upstream"
  - "starred (Focus) always wins over state in nirvana_to_todoist_priority per D-05"

patterns-established:
  - "Priority mapping tables live in app/core/priority.py as plain dicts + total .get(x, default) lookup functions"

requirements-completed: [D-01, D-03, D-05, D-06, D-09]

duration: ~25min
completed: 2026-07-24
---

# Phase 9 Plan 01: Nirvana config surface + priority mapping Summary

**Added TASK_PROVIDER/NIRVANA_PAT/NIRVANA_POLL_INTERVAL_SECS to Settings, a total two-axis Todoist-int <-> Nirvana (state, starred) priority mapping, and wired the new env vars through production docker-compose.yml + .env.production so the existing Todoist-only deployment doesn't crash once nirvana_pat becomes required.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 3 completed
- **Files modified:** 7 (5 in this repo, 2 external infra files)

## Accomplishments
- `Settings` gained 3 new fields (`task_provider` safe-default, `nirvana_pat` required/fail-fast, `nirvana_poll_interval_secs` default 3600) with 4 new passing tests
- `app/core/priority.py` gained `todoist_priority_to_nirvana` / `nirvana_to_todoist_priority`, total functions that never raise on unknown/open-vocabulary state strings, with 15 new passing tests (34/34 in the file)
- Production `docker-compose.yml` now passes `TASK_PROVIDER` and `NIRVANA_PAT` through to both `zoho-sync-web` and `zoho-sync-worker`; `.env.production` has an explicit `TASK_PROVIDER=todoist` default line (NIRVANA_PAT was already present)

## Task Commits

Each task was committed atomically (in the zoho-todoist-sync repo; Task 3's docker-compose.yml/.env.production edits are in a separate external infra path and are not part of this repo's history per plan instructions):

1. **Task 1: Add TASK_PROVIDER / NIRVANA_PAT / NIRVANA_POLL_INTERVAL_SECS to Settings** - `4b6c80e` (feat)
2. **Task 2: Add Todoist-int <-> Nirvana (state, starred) two-axis priority mapping** - `2ba10e3` (feat)
3. **Task 3: Wire TASK_PROVIDER/NIRVANA_PAT through docker-compose.yml and .env.production** - no commit in this repo (external files edited directly on disk, outside git); verified via acceptance-criteria greps below

**Plan metadata:** this SUMMARY.md commit

## Files Created/Modified
- `app/core/config.py` - Added `task_provider`, `nirvana_pat`, `nirvana_poll_interval_secs` fields to `Settings`
- `app/core/priority.py` - Added `TODOIST_TO_NIRVANA` dict + `todoist_priority_to_nirvana`/`nirvana_to_todoist_priority` functions
- `tests/conftest.py` - Added `NIRVANA_PAT` to `REQUIRED_ENV` so `complete_env` fixture stays valid
- `tests/unit/test_config.py` - 4 new tests for the 3 new Settings fields
- `tests/unit/test_priority.py` - 15 new tests for the two-axis mapping functions
- `/home/services/hetzner-vps/docker-compose.yml` (external) - Added `TASK_PROVIDER`/`NIRVANA_PAT` env passthrough to `zoho-sync-web` and `zoho-sync-worker`
- `/home/services/.env.production` (external) - Added `TASK_PROVIDER=todoist` line after existing `NIRVANA_PAT`

## Decisions Made
- Priority mapping functions key off canonical Todoist-int (not raw Zoho string) since `NormalisedTask.priority` is already converted upstream by `app/zoho/normalise.py` — this preserves the provider-agnostic `NormalisedTask`/`canonical_hash` invariant (documented as a deliberate deviation from RESEARCH.md's illustrative Zoho-string-keyed naming, already called out in the plan's `<interfaces>` section, not a new deviation).
- `TASK_PROVIDER=todoist` was appended as an explicit line in `.env.production` rather than left unset, per Task 3's action — matches D-01 (existing deployment must not break) while leaving the `nirvana` cutover as a future manual decision.

## Deviations from Plan

None - plan executed exactly as written. One environment quirk was handled without deviating from plan intent: this execution ran in a git worktree where `.planning/phases/` (gitignored by project convention) was empty; the plan/context/research files were copied in from the main working tree before execution so the plan's file contents could be read, per the worktree-isolation guidance already documented in the project's `.gitignore` comments. No plan content was altered.

## Issues Encountered
- `tests/unit/test_config.py::test_settings_succeeds_with_complete_env` fails independent of this plan's changes (asserts a hardcoded `todoist_project_id == "6gCPcWwM392GhXQh"` against the `complete_env` fixture's `"test-project-id"` value — a pre-existing test/fixture mismatch). Confirmed via `git stash` that this failure exists identically on the pre-plan commit. Left untouched as out of scope for this plan.

## User Setup Required
None - no external service configuration required. `NIRVANA_PAT` was already present in `/home/services/.env.production` from the spike session (D-03); `TASK_PROVIDER=todoist` was added as a safe default that requires no user action.

## Next Phase Readiness
`app/core/config.py` and `app/core/priority.py` now expose the fixed contract (`nirvana_pat`, `task_provider`, `nirvana_poll_interval_secs`, `todoist_priority_to_nirvana`, `nirvana_to_todoist_priority`) that Plan 03 (`app/nirvana/normalise.py`) and later plans import against. Production containers will not crash on `Settings()` validation once this plan's code ships, since docker-compose.yml already passes both new env vars through.

---
*Phase: 09-nirvana-taskprovider*
*Completed: 2026-07-24*
