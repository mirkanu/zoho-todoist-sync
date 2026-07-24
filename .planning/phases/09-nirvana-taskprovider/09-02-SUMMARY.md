---
phase: 09-nirvana-taskprovider
plan: 02
subsystem: database
tags: [sqlalchemy, alembic, postgres, migration]

# Dependency graph
requires: []
provides:
  - "SyncState.external_task_id + SyncState.provider columns (rename of todoist_task_id, provider CHECK-constrained to 'todoist'|'nirvana')"
  - "Alembic migration 002_add_provider_column chained from 001_initial_schema, with automatic backfill of provider='todoist' for all pre-existing rows"
affects: [09-03-worker-jobs, 09-04-reconciler, 09-05-webhooks-router, any plan touching sync_state or SyncState]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Alembic migrations: rename column first, then add_column with server_default so Postgres backfills existing rows in the same ALTER TABLE (no separate lossy UPDATE)"
    - "Live-DB migration tests spin up a throwaway, uniquely-named Postgres container via docker (never production zoho-sync-db) and skip cleanly when docker is unavailable"

key-files:
  created:
    - app/db/migrations/versions/002_add_provider_column.py
  modified:
    - app/db/models.py
    - tests/unit/test_models.py
    - tests/unit/test_migration.py
    - tests/unit/test_migration_and_app.py
    - scripts/migrate.py

key-decisions:
  - "sync_state.todoist_task_id renamed to external_task_id (not a new column) per CONTEXT.md D-12 — provider column added alongside, CHECK-constrained to ('todoist', 'nirvana')"
  - "provider column backfilled via server_default='todoist' on the same add_column statement Postgres applies to existing rows — no separate UPDATE needed, no risk of a lossy backfill step"
  - "scripts/migrate.py's SyncState(todoist_task_id=...) construction, the one call site outside this plan's declared file list, was fixed (renamed to external_task_id, provider='todoist' set explicitly) because it broke immediately once the column was renamed and is exercised directly by tests/unit/test_migration.py, which IS in this plan's scope"
  - "Live migration tests provision a throwaway, disposable Postgres container (unique name, ephemeral port) rather than reusing any existing container on the VPS — this satisfies the instruction to test against a test/dev database only, never zoho-sync-db (production, running since 2026-05-01)"
  - "The two live-DB tests (upgrade-head structure check, pre-migration-row backfill check) were added to tests/unit/test_migration_and_app.py rather than split across test_migration.py, since that file's established convention (per its own docstring) is structural/alembic-scaffold testing; test_migration.py is scoped to scripts/migrate.py's algorithm and only needed two small assertion updates to match the renamed column"

patterns-established:
  - "New sync_state.* call sites (jobs.py, reconciler.py, webhooks/router.py) will fail with AttributeError/TypeError until their own plans in this phase update them to external_task_id/provider — this is expected and by design (Pitfall 1 in 09-RESEARCH.md), not a regression introduced by this plan"

requirements-completed: [D-12]

# Metrics
duration: ~25min
completed: 2026-07-24
---

# Phase 09 Plan 02: sync_state Schema Migration Summary

**Renamed `sync_state.todoist_task_id` to `external_task_id`, added a CHECK-constrained `provider` column, and proved via a real throwaway Postgres container that existing production-shaped rows backfill to `provider='todoist'` with zero data loss.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 completed
- **Files modified:** 6 (1 created, 5 modified)

## Accomplishments
- `SyncState` ORM model now exposes `external_task_id` + `provider` (CHECK-constrained to `'todoist'`/`'nirvana'`), replacing the Todoist-specific `todoist_task_id` name
- New Alembic migration `002_add_provider_column`, chained from `001_initial_schema`, renames the column and backfills `provider='todoist'` for every existing row via `server_default` on the same `ALTER TABLE ADD COLUMN` (no separate UPDATE pass, no data-loss window)
- Verified against a real, disposable Postgres container (not production `zoho-sync-db`): a row inserted under the *old* column name before running migration 002 ends up with `provider='todoist'` after `alembic upgrade head` — the exact D-12 backfill scenario, proven end-to-end rather than asserted structurally

## Task Commits

Each task was committed atomically:

1. **Task 1: Update SyncState ORM model** - `4b7eaa1` (refactor)
2. **Task 2: Write Alembic migration 002_add_provider_column** - `c6b17db` (feat)

## Files Created/Modified
- `app/db/models.py` - `SyncState.todoist_task_id` renamed to `external_task_id`; new `provider` column (`String(16)`, `nullable=False`, `server_default="todoist"`); index renamed `idx_sync_state_external_task_id`; new `CheckConstraint("provider IN ('todoist', 'nirvana')", name="ck_sync_state_provider")`
- `app/db/migrations/versions/002_add_provider_column.py` - New migration: `alter_column` rename, `add_column` with backfilling `server_default`, `create_check_constraint`, index drop/recreate; `downgrade()` reverses in the correct order (index → constraint → column drop → column rename-back)
- `tests/unit/test_models.py` - Updated column-set assertion; added tests for the `provider` column's length/default and the `ck_sync_state_provider` CHECK constraint; renamed the index-existence test
- `tests/unit/test_migration_and_app.py` - Added structural tests for migration 002 (revision id, down_revision chaining, upgrade/downgrade callables) mirroring the existing 001 test pattern; added a `pg_test_db_url` fixture that provisions/tears down a throwaway Postgres container and two live tests exercising the real migration (fresh-DB column check; pre-existing-row backfill check)
- `tests/unit/test_migration.py` - Updated two assertions from `.todoist_task_id` to `.external_task_id` to match the renamed column (these tests exercise `scripts/migrate.py`, not the migration file itself)
- `scripts/migrate.py` - `_upsert_sync_state` now constructs `SyncState(external_task_id=..., provider="todoist", ...)` instead of the now-invalid `todoist_task_id=...` keyword

## Decisions Made
- Backfill via `server_default` on `add_column`, not a follow-up `UPDATE` statement — Postgres applies the default to every existing row as part of the same DDL statement, which is both simpler and removes any window where rows could exist with a null/incorrect `provider`.
- Live-DB tests use a uniquely-named, ephemeral-port Postgres container spun up and torn down entirely within a pytest fixture, and skip cleanly (not fail) when Docker is unavailable — this keeps the test suite portable while still proving the migration works against a real database in this environment, and guarantees the production `zoho-sync-db` container was never touched.
- Fixed `scripts/migrate.py`'s `SyncState(todoist_task_id=...)` construction even though it wasn't in this plan's declared `files_modified` list — it's the one call site outside `app/db/models.py` that directly constructs a `SyncState` row, and it's exercised by `tests/unit/test_migration.py`, which the plan explicitly requires to pass. Leaving it broken would fail this plan's own acceptance criteria.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed scripts/migrate.py's now-invalid SyncState(todoist_task_id=...) call**
- **Found during:** Task 2 verification (`pytest tests/unit/test_migration.py`)
- **Issue:** `scripts/migrate.py`'s `_upsert_sync_state` constructs `SyncState(todoist_task_id=...)` — a keyword argument that no longer exists on the model after Task 1's rename, causing a `TypeError` at row-construction time. This file wasn't in the plan's declared `files_modified` list but is directly exercised by `tests/unit/test_migration.py`.
- **Fix:** Renamed the kwarg to `external_task_id=todoist_task_id` (kept the local parameter name for minimal diff) and added `provider="todoist"` explicitly, since this script only ever writes Todoist-sourced rows. Updated two assertions in `tests/unit/test_migration.py` from `.todoist_task_id` to `.external_task_id` to match.
- **Files modified:** `scripts/migrate.py`, `tests/unit/test_migration.py`
- **Verification:** `pytest tests/unit/test_migration.py tests/unit/test_migration_and_app.py -x` — 52 passed
- **Committed in:** `c6b17db` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (blocking)
**Impact on plan:** Necessary for the plan's own acceptance criteria (`pytest tests/unit/test_migration.py ... -x` exits 0) to be met. No scope creep — this is the narrowest fix that keeps the declared test files green without touching `jobs.py`, `reconciler.py`, or `webhooks/router.py`, which remain other plans' responsibility.

## Issues Encountered
- Running the broader `pytest tests/unit/` (not just this plan's target files) surfaces 10 pre-existing failures in `test_reconciler.py`, `test_webhooks.py`, and `test_worker_jobs.py` — all `AttributeError`/`TypeError` from those files' direct references to `SyncState.todoist_task_id` / `state.todoist_task_id`. This is expected: those files are the explicit responsibility of other plans in this phase's wave (per 09-RESEARCH.md's call-site map and Pitfall 1), not this plan. They were left untouched.
- The broader `pytest tests/unit/` run also has 4 unrelated pre-existing collection errors (`test_backfill_descriptions.py`, `test_todoist_description.py`, `test_todoist_writer.py`, `test_zoho_writer.py`) caused by real `ZOHO_*` env vars already present in the shell environment partially satisfying `Settings`, then failing pydantic validation on the remaining required fields. Confirmed via `git stash` that this failure exists identically on the pre-plan `HEAD` — unrelated to this plan's changes, not fixed (out of scope).

## User Setup Required
None - no external service configuration required. No production database was touched; migration 002 has not been run against `zoho-sync-db`.

## Next Phase Readiness
- `app/db/models.py` and migration `002_add_provider_column` are the final column-name contract (`external_task_id`, `provider`) that plans 09-03 (worker jobs), 09-04 (reconciler), and 09-05 (webhooks router) — and any other plan touching `sync_state` — should implement against.
- Migration 002 has been verified to run cleanly and backfill correctly against a real Postgres instance, but has **not** been applied to the production `zoho-sync-db` container — deployment/rollout of this migration against production is not part of this plan and should be sequenced after all downstream code (jobs.py, reconciler.py, webhooks/router.py) is updated to use the new column names, to avoid a window where running code references a column that no longer exists.

---
*Phase: 09-nirvana-taskprovider*
*Completed: 2026-07-24*
