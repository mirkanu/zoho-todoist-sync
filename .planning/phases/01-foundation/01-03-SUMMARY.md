---
phase: 01-foundation
plan: 03
subsystem: database
tags:
  - postgres
  - sqlalchemy
  - alembic
  - fastapi
  - schema

requires:
  - phase: 01-foundation/01-01
    provides: app.core.config.Settings + settings singleton (needed by env.py and main.py)

provides:
  - SQLAlchemy 2.x ORM models: SyncState, SyncEvent, KVStore (app.db.models.Base)
  - Alembic migration scaffold: env.py, script.py.mako, versions/
  - Initial schema migration 001_initial_schema with all 3 tables and 3 indexes
  - FastAPI app stub with asynccontextmanager lifespan (app.main.app)
  - app.core.logging: configure_logging + get_logger (structlog with stdlib fallback)

affects:
  - All phases that write to sync_state, sync_events, kv_store
  - Phase 02+ (Zoho/Todoist API clients that use the ORM models)
  - Phase 06 (lifespan wiring with DB pool startup)

tech-stack:
  added:
    - sqlalchemy[asyncio]==2.0.49
    - alembic==1.18.4
    - fastapi==0.136.0
    - structlog (structured logging)
  patterns:
    - "SQLAlchemy 2.x DeclarativeBase pattern (not legacy Base = declarative_base())"
    - "Alembic async env.py: async_engine_from_config + asyncpg driver, no hardcoded URL"
    - "FastAPI asynccontextmanager lifespan (NOT deprecated on_event)"
    - "Structlog configure_logging with stdlib fallback for portability"

key-files:
  created:
    - app/db/__init__.py
    - app/db/models.py
    - app/db/migrations/__init__.py
    - app/db/migrations/env.py
    - app/db/migrations/script.py.mako
    - app/db/migrations/versions/__init__.py
    - app/db/migrations/versions/001_initial_schema.py
    - alembic.ini
    - app/core/logging.py
    - app/main.py
    - tests/unit/test_models.py
    - tests/unit/test_migration_and_app.py
  modified: []

key-decisions:
  - "Migration table names on same line as op.create_table() call to satisfy grep-based acceptance criteria (single-line format required)"
  - "app/core/logging.py created in this plan (wave 2 parallel with plan 02) with structlog + stdlib fallback; plan 02 will produce the canonical version"
  - "Alembic sqlalchemy.url set dynamically from settings.database_url in env.py; alembic.ini contains no literal database URL"

patterns-established:
  - "SQLAlchemy 2.x: class Base(DeclarativeBase): pass — not the legacy declarative_base() factory"
  - "Async Alembic migrations: async_engine_from_config with asyncpg, NullPool for migration runs"
  - "FastAPI lifespan: @asynccontextmanager pattern, yields after startup, logs safe fields only"

requirements-completed:
  - INFRA-1
  - INFRA-2

duration: 12min
completed: "2026-04-23"
---

# Phase 1 Plan 3: Postgres Schema and FastAPI Stub Summary

**SQLAlchemy 2.x ORM models (SyncState, SyncEvent, KVStore) with Alembic async migration scaffold, initial schema migration with 3 indexes, and FastAPI stub with asynccontextmanager lifespan logging zoho_region + todoist_task_id_field.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-04-23T17:10:00Z
- **Completed:** 2026-04-23T17:22:00Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 12 created, 0 modified

## Accomplishments

- Three SQLAlchemy 2.x ORM models with exact column types from the schema contract: `String(64)` for `last_hash`, `String(32)` for `action`/`source`, `JSONB` for `detail`, `DateTime(timezone=True)` throughout
- Alembic async migration scaffold: `env.py` reads `DATABASE_URL` from `settings.database_url` at runtime — no hardcoded credentials anywhere; uses `asyncpg` driver via `async_engine_from_config`
- `001_initial_schema.py` migration creates all three tables and the three required indexes (`idx_sync_state_todoist_task_id`, `idx_sync_events_created_at`, `idx_sync_events_zoho_task_id_created_at`) with correct `downgrade()` in reverse order
- FastAPI `app` stub using `asynccontextmanager` lifespan (never `on_event`), logs only safe fields: `zoho_region`, `todoist_task_id_field`, `log_level`
- 35 tests across two test modules (16 schema-shape + 19 migration/app structural), all passing without a live database

## Task Commits

Each task was committed atomically with RED/GREEN TDD gates:

1. **Task 1 RED: Schema tests** - `418269f` (test)
2. **Task 1 GREEN: SQLAlchemy models** - `ba0e088` (feat)
3. **Task 2 RED: Migration + app tests** - `5884f13` (test)
4. **Task 2 GREEN: Alembic scaffold + FastAPI stub** - `f8010c4` (feat)

## Files Created/Modified

| File | Purpose |
|------|---------|
| `app/db/__init__.py` | DB subpackage init |
| `app/db/models.py` | SyncState, SyncEvent, KVStore ORM models |
| `app/db/migrations/__init__.py` | Migrations package init |
| `app/db/migrations/env.py` | Alembic async env — reads DATABASE_URL from settings |
| `app/db/migrations/script.py.mako` | Alembic migration template |
| `app/db/migrations/versions/__init__.py` | Versions package init |
| `app/db/migrations/versions/001_initial_schema.py` | Initial schema DDL + 3 indexes |
| `alembic.ini` | Alembic config pointing to app/db/migrations |
| `app/core/logging.py` | configure_logging + get_logger (structlog + stdlib fallback) |
| `app/main.py` | FastAPI app with asynccontextmanager lifespan |
| `tests/unit/test_models.py` | 16 schema-shape tests (no live DB required) |
| `tests/unit/test_migration_and_app.py` | 19 structural tests for migration + app |

## Decisions Made

- **Migration inline table names:** `op.create_table("sync_state", ...)` format (table name on same line) required to satisfy the plan's grep-based acceptance criteria. The plan's `grep -q "op.create_table(\s*\"sync_state\""` without `-E` flag only matches single-line occurrences.
- **app/core/logging.py created here:** Plans 02 and 03 run in wave 2 in parallel. Plan 02 owns `logging.py` as its canonical output, but `app/main.py` (plan 03) imports it. Created a compatible implementation with structlog (primary) and stdlib (fallback). Plan 02's version will overwrite this if needed — the interface contract (`configure_logging(level)`, `get_logger(name)`) is identical.
- **No alembic.ini DATABASE_URL:** Accepted per design — `env.py` sets it programmatically from `settings.database_url`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Created app/core/logging.py as wave-2 parallel dependency**

- **Found during:** Task 2 (FastAPI stub implementation)
- **Issue:** `app/main.py` imports `from app.core.logging import configure_logging, get_logger`, but `app/core/logging.py` is produced by plan 02 running in parallel in the same wave. The file doesn't exist in the worktree at execution time.
- **Fix:** Implemented `app/core/logging.py` with `configure_logging()` and `get_logger()` using structlog (with stdlib fallback for environments where structlog isn't installed). The interface matches what plan 02 will produce.
- **Files modified:** `app/core/logging.py`
- **Verification:** `from app.main import app` succeeds; `test_main_py_importable_with_env` passes
- **Committed in:** `f8010c4`

---

**Total deviations:** 1 auto-fixed (Rule 3 — blocking parallel dependency)
**Impact on plan:** Required for `app/main.py` to be importable. No scope creep — same interface as plan 02's planned output.

## Issues Encountered

None beyond the blocking parallel dependency described above.

## Known Stubs

None. All models map to real column types. The FastAPI app stub intentionally has no routes — routes are added in later phases per design.

## Threat Flags

None found beyond the plan's documented threat model:
- T-1-12 mitigated: No hardcoded DATABASE_URL anywhere; `env.py` reads from `settings.database_url`
- T-1-13 mitigated: `app/main.py` logs only `zoho_region`, `todoist_task_id_field`, `log_level`; negative grep `! grep -q "log.*=.*settings[^_.]"` passes
- T-1-14 mitigated: `String(length=64)` in both ORM model and migration; 16 schema tests enforce this
- T-1-15 mitigated: All 3 indexes in migration + tested in `test_models.py`

## TDD Gate Compliance

- Task 1 RED gate: commit `418269f` — 16 tests written, fail with `ModuleNotFoundError: No module named 'app.db'`
- Task 1 GREEN gate: commit `ba0e088` — all 16 tests pass
- Task 2 RED gate: commit `5884f13` — 19 tests written, fail with `AssertionError: alembic.ini must exist`
- Task 2 GREEN gate: commit `f8010c4` — all 35 tests pass (16 + 19)

## Next Phase Readiness

- `app.db.models.Base.metadata` ready for any phase that needs to reference table schema
- `alembic upgrade head` against a live Railway Postgres will create all three tables and indexes (manual verification step documented in `01-VALIDATION.md`)
- `app.main.app` is a valid FastAPI instance; later phases add routes via `app.include_router()`
- `app.core.logging.get_logger(__name__)` pattern established for all modules

## Self-Check: PASSED

All files confirmed present. All commits confirmed in git log (418269f, ba0e088, 5884f13, f8010c4).

---
*Phase: 01-foundation*
*Completed: 2026-04-23*
