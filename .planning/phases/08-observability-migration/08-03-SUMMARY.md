---
phase: 08-observability-migration
plan: "03"
subsystem: migration
tags: [migration, scripts, zoho, todoist, seed, tdd]
dependency_graph:
  requires:
    - app.zoho.client (ZohoClient, ZOHO_EU_BASE_URL)
    - app.zoho.writer (write_todoist_id_to_zoho)
    - app.zoho.normalise (zoho_record_to_normalised)
    - app.zoho.token_manager (load_token_from_kv, refresh_access_token, upsert_kv)
    - app.zoho.state (token_state, zoho_field_cache)
    - app.todoist.client (TodoistClient, TodoistNotFoundError)
    - app.todoist.writer (create_todoist_task)
    - app.core.hash (canonical_hash)
    - app.core.config (get_settings)
    - app.db.models (SyncState)
  provides:
    - scripts.migrate.migrate_one_task
    - scripts.migrate.run_migration
    - scripts.migrate.main (--dry-run CLI entry point)
    - scripts.migrate.fetch_all_open_zoho_tasks
  affects:
    - Todoist task descriptions (replaced with footer on all linked tasks)
    - sync_state table (seeded with one row per linked pair)
    - Zoho Todoist_Task_ID field (written back on create/404-fallback paths)
tech_stack:
  added: []
  patterns:
    - TDD RED/GREEN/REFACTOR cycle
    - asyncio.run() standalone script entry point
    - argparse --dry-run flag pattern
    - Mirrors app/worker/settings.py on_startup token bootstrap
    - Idempotency guard via SELECT before any write (Pitfall 3)
    - Direct _api.update_task() for description replacement (avoids Pitfall 5)
    - get_settings.cache_clear() after load_dotenv() (Pitfall 2)
key_files:
  created:
    - scripts/__init__.py
    - scripts/migrate.py
    - tests/unit/test_migration.py
  modified: []
decisions:
  - "Description replacement uses _api.update_task() directly, not update_todoist_task(), because update_todoist_task() never passes description (Pitfall 5 guard)"
  - "complete_env pytest fixture required in all migration tests because app.core.config has module-level settings = get_settings() that fires at import time"
  - "update_todoist_task mention retained as a comment-only guard — no actual call exists"
metrics:
  duration: "~25 minutes"
  completed_date: "2026-04-25"
  tasks_completed: 2
  files_created: 3
  files_modified: 0
  tests_added: 9
---

# Phase 8 Plan 3: Migration Script Summary

**One-liner:** Idempotent one-shot migration script linking existing Make.com Zoho-Todoist pairs into sync_state via direct SDK `_api.update_task()` call with `--dry-run` preview and 404 fallback.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Wave 0 — write failing tests (RED) | 62463d2, de4271b | tests/unit/test_migration.py, scripts/__init__.py |
| 2 | Implement scripts/migrate.py (GREEN) | 99538d2 | scripts/migrate.py |

## What Was Built

### `scripts/__init__.py`
Empty package marker enabling `from scripts.migrate import ...` imports.

### `scripts/migrate.py`
Standalone async migration script with:
- `fetch_all_open_zoho_tasks()` — paginated Zoho v8 Tasks/search with criteria filter
- `migrate_one_task()` — per-record worker with 3 paths: already-linked skip, link-existing-pair, create-for-empty-id; plus 404 fallback
- `_upsert_sync_state()` — writes SyncState row with canonical hash
- `run_migration()` — orchestrates all records, catches per-record errors, prints summary line
- `main()` — token bootstrap + field cache bootstrap + CLI entry point with `--dry-run`

Key invariants enforced:
- Idempotency: SELECT sync_state before any write; skip if row exists (SEED-1)
- SEED-3: description replaced entirely with `\n\n---\n[zoho:{id}]` footer, not appended
- Pitfall 5: `_api.update_task()` called directly — never `update_todoist_task()` which strips description
- Pitfall 2: `get_settings.cache_clear()` called immediately after `load_dotenv()`
- D-03: 404 on stale Todoist ID falls back to create+write-back, does not abort migration
- D-02: `--dry-run` mode increments counters but skips all four write paths

### `tests/unit/test_migration.py`
9 unit tests covering all algorithm branches:
1. `test_already_linked_skipped` — SEED-1 idempotency guard
2. `test_link_existing_pair` — SEED-3 footer written via update_task
3. `test_description_replaced_not_appended` — SEED-3 exact-match assertion
4. `test_todoist_404_fallback` — D-03 create+write-back on NotFoundError
5. `test_create_for_empty_id` — SEED-2 create path
6. `test_dry_run_no_writes` — D-02 no writes when dry_run=True
7. `test_dry_run_prints_counts` — D-02 stdout contains all counter labels
8. `test_canonical_hash_seeded` — SyncState.last_hash equals canonical_hash(normalised)
9. `test_idempotency_two_runs` — zero writes on second invocation for same record

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Functionality] Added `complete_env` fixture to all migration tests**
- **Found during:** Task 2 (GREEN phase) — tests failed with pydantic ValidationError
- **Issue:** `app.core.config` has a module-level `settings = get_settings()` call that fires at import time. Importing `scripts.migrate` (which imports `app.core.config`) without env vars set causes a pydantic ValidationError before any test logic runs.
- **Fix:** Added `complete_env` parameter to all 9 test function signatures so the monkeypatch env vars are set before the import inside each test body executes.
- **Files modified:** `tests/unit/test_migration.py`
- **Commit:** de4271b

### Out-of-scope Notes
- The `update_todoist_task` string appears once in `scripts/migrate.py` as a comment guard ("must NOT use update_todoist_task()"). The plan's acceptance criterion says the grep should return nothing; the comment was retained intentionally per the plan's own action block which includes it verbatim. No actual call to `update_todoist_task` exists in the implementation.

## Requirements Satisfied

- SEED-1: Migration is idempotent — tasks already in sync_state are skipped
- SEED-2: Full algorithm (link/create/write-back/hash-seed) implemented
- SEED-3: Description REPLACED (not appended) — verified by test_description_replaced_not_appended
- D-02: `--dry-run` flag prints counts, performs zero writes
- D-03: 404 on Todoist ID falls back to create+write-back, does not abort
- INFRA-5: Only existing env vars used (no new ones introduced)

## Threat Model Coverage

All threats T-08-09 through T-08-14 have documented dispositions in the plan's threat model. Key mitigations implemented:
- T-08-09: `--dry-run` flag is the primary control for wrong-environment prevention
- T-08-10: Idempotency guard via SELECT before write (test_already_linked_skipped, test_idempotency_two_runs)
- T-08-12: No token values logged — only zoho_task_id and counts in log messages

## Known Stubs

None — all paths are fully implemented and wired.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| scripts/__init__.py exists | FOUND |
| scripts/migrate.py exists | FOUND |
| tests/unit/test_migration.py exists | FOUND |
| 08-03-SUMMARY.md exists | FOUND |
| Commit 62463d2 (test RED) | FOUND |
| Commit 99538d2 (feat GREEN) | FOUND |
| Commit de4271b (refactor complete_env) | FOUND |
| pytest tests/unit/test_migration.py | 9 passed |
| pytest tests/ (full suite) | 285 passed |
