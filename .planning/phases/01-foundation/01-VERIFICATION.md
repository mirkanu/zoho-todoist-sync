---
phase: 01-foundation
verified: 2026-04-23T18:30:00Z
status: passed
score: 16/16
overrides_applied: 0
deferred:
  - truth: "INFRA-1: Service runs as two Railway services (web + worker)"
    addressed_in: "Phase 5"
    evidence: "Phase 5 success criteria: sync_task job function, arq worker, dedup enqueue — and Phase 6: webhook endpoints. Phase 1 delivers only the FastAPI stub; arq worker is Phase 5."
  - truth: "INFRA-3: Redis on Railway for arq job queue and deduplication"
    addressed_in: "Phase 5"
    evidence: "Traceability table: INFRA-3 spans Phase 1, Phase 5. Phase 1 delivers only the redis_url settings field. arq/Redis wiring is Phase 5 work."
  - truth: "INFRA-6: Zoho OAuth token auto-refresh at 50 min"
    addressed_in: "Phase 2"
    evidence: "Traceability table: INFRA-6 spans Phase 1, Phase 2. Phase 1 delivers zoho_region/zoho_client_id/secret/refresh_token settings fields. OAuth client implementation is Phase 2."
  - truth: "INFRA-7: Read Todoist Task ID field api_name from Zoho settings endpoint at startup"
    addressed_in: "Phase 2"
    evidence: "Traceability table: INFRA-7 spans Phase 1, Phase 2. Phase 1 delivers zoho_todoist_task_id_field settings field (empty default). Actual startup fetch is Phase 2."
---

# Phase 1: Foundation Verification Report

**Phase Goal:** Schema, config, canonical hash, payload normalisation, and correctness-critical unit tests
**Verified:** 2026-04-23T18:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `Settings` class raises `ValidationError` at import when any required env var is missing (INFRA-5 fail-fast) | VERIFIED | `settings = Settings()` at line 28 of `app/core/config.py`; `test_settings_raises_on_missing_required_var` subprocess test exists and passes |
| 2 | `settings.zoho_terminal_statuses_list` returns a Python list parsed from the comma-separated env var, stripping whitespace (EDGE-4 config) | VERIFIED | `zoho_terminal_statuses_list` property at lines 21-23 of `app/core/config.py`; 3 tests cover single, multi, and whitespace cases |
| 3 | `canonical_hash(task)` returns the same SHA-256 hex for tasks whose `Due_Date` was `"2026-05-01T00:00:00+05:30"` vs `"2026-05-01"` (LOOP-2) | VERIFIED | `normalise_due_date` uses `datetime.fromisoformat().date()`; `test_same_logical_task_same_hash` confirms |
| 4 | `canonical_hash` returns the same hex for NFC and NFD Unicode forms of `"café"` (LOOP-2) | VERIFIED | `normalise_title` applies `unicodedata.normalize("NFC", ...)`; `test_unicode_nfc_nfd_same_hash` confirms |
| 5 | `canonical_hash` returns the same hex for CRLF and LF line endings in title (LOOP-2) | VERIFIED | `normalise_title` replaces `\r\n` and `\r` with `\n`; `test_crlf_same_as_lf` confirms |
| 6 | `canonical_hash` returns the same hex for `due_date=None` and `due_date=""` (LOOP-2) | VERIFIED | `normalise_due_date` returns `None` for both; `test_null_due_date_stable` confirms |
| 7 | `zoho_to_todoist_priority("Highest") == 4` — priority is NOT inverted (SYNC-2) | VERIFIED | `ZOHO_TO_TODOIST = {"Highest": 4, ...}` in `app/core/priority.py`; `test_highest_is_NOT_todoist_1` explicit regression guard passes |
| 8 | `NormalisedTask` has exactly 4 fields: `title`, `due_date`, `priority`, `is_completed` — no `description`, no `labels` (SYNC-7, SYNC-9) | VERIFIED | Frozen dataclass in `app/core/normalise.py` lines 10-15; `test_label_not_in_hash` confirms via `dataclasses.fields()` |
| 9 | `ZOHO_ID_RE` regex extracts `\d+` from `[zoho:1234567890]` footer (SYNC-5) | VERIFIED | `ZOHO_ID_RE = re.compile(r"\[zoho:(\d+)\]")` exported at module level; `test_zoho_id_regex` confirms |
| 10 | `strip_footer` removes `\n\n---\n[zoho:ID]` trailer; `strip_footer(None) == ""` (SYNC-5) | VERIFIED | `FOOTER_RE.sub("", description).strip()`; `test_strip_footer_basic`, `test_strip_footer_none`, `test_strip_footer_no_footer` all pass |
| 11 | `configure_logging(level)` with INFO/DEBUG/WARNING/unknown does not raise (OBS-5) | VERIFIED | `getattr(logging, level.upper(), logging.INFO)` fallback; 8 logging tests all pass |
| 12 | `SyncState`, `SyncEvent`, `KVStore` defined as SQLAlchemy 2.x `DeclarativeBase` subclasses (INFRA-2) | VERIFIED | `class Base(DeclarativeBase)` in `app/db/models.py`; all three classes inherit Base; `test_all_tables_in_base_metadata` confirms |
| 13 | `SyncState.last_hash` column is `String(64)` (SHA-256 hex length) | VERIFIED | `Column(String(64), nullable=False)` at line 17; `test_sync_state_last_hash_is_string_64` confirms |
| 14 | `SyncEvent.detail` column is PostgreSQL `JSONB` | VERIFIED | `Column(JSONB, nullable=True)` at line 34; `test_sync_events_detail_is_jsonb` confirms |
| 15 | Alembic migration `001_initial_schema.py` creates all 3 tables and 3 required indexes (INFRA-2) | VERIFIED | Migration creates sync_state, sync_events, kv_store with all 3 indexes; `downgrade()` reverses them; migration imports cleanly |
| 16 | `app/main.py` FastAPI app uses `asynccontextmanager` lifespan, logs `zoho_region` and `todoist_task_id_field` safely (INFRA-1 partial) | VERIFIED | `@asynccontextmanager async def lifespan`, `app = FastAPI(lifespan=lifespan)`; never logs settings object whole; `test_main_py_no_deprecated_on_event` confirms |

**Score:** 16/16 truths verified

### Deferred Items

Items not yet met but explicitly addressed in later milestone phases.

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | INFRA-1: arq worker service (full two-service Railway deploy) | Phase 5 | Phase 5 goal: "sync_task job function, dedup enqueue, per-task lock, retry config" |
| 2 | INFRA-3: Redis arq queue and deduplication wiring | Phase 5 | Traceability: INFRA-3 spans Phase 1, Phase 5. Phase 1 provides only `redis_url` settings field |
| 3 | INFRA-6: Zoho OAuth token auto-refresh at 50 min | Phase 2 | Traceability: INFRA-6 spans Phase 1, Phase 2. Phase 1 provides OAuth credential fields |
| 4 | INFRA-7: Startup fetch of Todoist Task ID field `api_name` from Zoho settings endpoint | Phase 2 | Traceability: INFRA-7 spans Phase 1, Phase 2. Phase 1 provides `zoho_todoist_task_id_field` settings field with empty default |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/core/config.py` | Settings class + module-level singleton | VERIFIED | `class Settings(BaseSettings)` + `settings = Settings()` at module level |
| `app/core/normalise.py` | NormalisedTask + normalise functions + FOOTER_RE + ZOHO_ID_RE | VERIFIED | All exports present; frozen dataclass with exactly 4 fields |
| `app/core/hash.py` | `canonical_hash(NormalisedTask) -> str` | VERIFIED | SHA-256 hex, sort_keys=True, ensure_ascii=False |
| `app/core/priority.py` | ZOHO_TO_TODOIST + TODOIST_TO_ZOHO + helper functions | VERIFIED | `None` and `""` explicit keys; Highest→4 confirmed |
| `app/core/logging.py` | `configure_logging` + `get_logger` | VERIFIED | JSONRenderer/ConsoleRenderer switch; unknown-level fallback |
| `app/db/models.py` | SyncState, SyncEvent, KVStore ORM models | VERIFIED | DeclarativeBase pattern; String(64), String(32), JSONB, DateTime(timezone=True) |
| `app/db/migrations/versions/001_initial_schema.py` | Initial schema migration | VERIFIED | 3 tables, 3 indexes; upgrade + downgrade callable |
| `app/db/migrations/env.py` | Alembic async env pulling DATABASE_URL from settings | VERIFIED | `async_engine_from_config`; no hardcoded credentials |
| `alembic.ini` | Alembic config pointing to migrations dir | VERIFIED | `script_location = app/db/migrations` |
| `app/main.py` | FastAPI app with asynccontextmanager lifespan | VERIFIED | No deprecated `on_event`; logs only safe fields |
| `pyproject.toml` | Project metadata + pytest config | VERIFIED | `[tool.pytest.ini_options]` with testpaths + asyncio_mode=auto |
| `requirements.txt` | Pinned runtime dependencies | VERIFIED | All 13 packages with exact pins including `pydantic-settings==2.14.0` |
| `requirements-dev.txt` | Dev dependency pins | VERIFIED | `pytest==9.0.2`, `pytest-asyncio` |
| `.python-version` | Railway runtime pin | VERIFIED | `3.12` |
| `.gitignore` | Git ignores `.env` | VERIFIED | `^.env$` confirmed |
| `.env.example` | Documents all required env vars | VERIFIED | All required keys with placeholder values |
| `tests/unit/test_config.py` | Config fail-fast tests | VERIFIED | 9 tests covering INFRA-5, INFRA-6, INFRA-7, INFRA-4, EDGE-4 |
| `tests/unit/test_normalise.py` | Normalisation + footer tests | VERIFIED | 13 tests covering LOOP-2, SYNC-5 |
| `tests/unit/test_hash.py` | Canonical hash stability tests | VERIFIED | 8 tests covering LOOP-1, LOOP-2, SYNC-7, SYNC-9 |
| `tests/unit/test_priority.py` | Priority mapping tests | VERIFIED | 18 tests with explicit anti-inversion regression guard |
| `tests/unit/test_logging.py` | Structured logging tests | VERIFIED | 8 tests covering OBS-5 |
| `tests/unit/test_models.py` | Schema shape tests | VERIFIED | 15 tests — no live DB required |
| `tests/unit/test_migration_and_app.py` | Migration + app structural tests | VERIFIED | 19 structural tests |
| `tests/conftest.py` | Shared pytest fixtures | VERIFIED | `complete_env` fixture with all required dummy vars |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `app/core/config.py` | `pydantic_settings.BaseSettings` | class inheritance | WIRED | `class Settings(BaseSettings)` confirmed |
| `tests/unit/test_config.py` | `app.core.config.Settings` | import | WIRED | `from app.core.config import Settings` confirmed |
| `app/core/hash.py` | `app/core/normalise.py::NormalisedTask` | import | WIRED | `from .normalise import NormalisedTask` at line 3 |
| `tests/unit/test_hash.py` | `app.core.normalise`, `app.core.hash`, `app.core.priority` | imports | WIRED | All three imports confirmed at lines 2-5 |
| `app/db/migrations/env.py` | `app.core.config.settings` | import | WIRED | `from app.core.config import settings` at line 6 |
| `app/db/migrations/env.py` | `app.db.models.Base` | import + target_metadata | WIRED | `from app.db.models import Base` + `target_metadata = Base.metadata` |
| `app/main.py` | `app.core.config.settings` + `app.core.logging` | import + lifespan logs | WIRED | `configure_logging(settings.log_level)` at line 6 |

### Data-Flow Trace (Level 4)

This phase contains no components that render dynamic data to users — all artifacts are pure-function modules, config, and schema definitions. The canonical hash is computed from `NormalisedTask` fields (all populated by callers at runtime); the settings singleton is populated from environment variables. No hollow props or disconnected data sources exist.

| Artifact | Data Variable | Source | Status |
|----------|---------------|--------|--------|
| `app/core/config.py` | All settings fields | OS environment via pydantic-settings | FLOWING — validated at import |
| `app/core/hash.py` | `task: NormalisedTask` | Caller provides at runtime | FLOWING — function has no internal state |
| `app/core/normalise.py` | `raw: str | None` inputs | Caller provides at runtime | FLOWING — pure transforms |

### Behavioral Spot-Checks

| Behavior | Result | Status |
|----------|--------|--------|
| 92 unit tests in `tests/unit/` pass | `92 passed in 1.42s` | PASS |
| `settings = Settings()` at module level (fail-fast) | Line 28 of `app/core/config.py` exists | PASS |
| `canonical_hash` returns 64-char hex | Verified by `test_hash_returns_64_hex_chars` | PASS |
| Priority not inverted: Highest→4 | Verified by `test_highest_is_NOT_todoist_1` regression guard | PASS |
| NormalisedTask has exactly 4 fields | Verified by `test_label_not_in_hash` via `dataclasses.fields()` | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| INFRA-1 | 01-03 | Two Railway services (web + worker) | PARTIAL | FastAPI stub delivered; arq worker deferred to Phase 5. Traceability confirms multi-phase. |
| INFRA-2 | 01-03 | Postgres schema with all tables and indexes | SATISFIED | 3 ORM models + migration with all 3 indexes; 15 schema-shape tests pass |
| INFRA-3 | 01-01 | Redis on Railway | PARTIAL | `redis_url` settings field declared; arq/Redis wiring deferred to Phase 5. Traceability confirms multi-phase. |
| INFRA-4 | 01-01 | Python 3.12, FastAPI, arq, todoist-api-python, zohocrmsdk | SATISFIED | `.python-version` = 3.12; all packages pinned in `requirements.txt` |
| INFRA-5 | 01-01 | All secrets as Railway env vars with fail-fast validation | SATISFIED | Full Settings class; `settings = Settings()` at module level raises ValidationError on missing var |
| INFRA-6 | 01-01 | Zoho OAuth EU region, auto-refresh at 50 min | PARTIAL | Credential fields present in Settings; OAuth client implementation deferred to Phase 2. Traceability confirms multi-phase. |
| INFRA-7 | 01-01 | Read Todoist Task ID field api_name at startup | PARTIAL | `zoho_todoist_task_id_field` settings field with empty default; startup fetch deferred to Phase 2. Traceability confirms multi-phase. |
| LOOP-1 | 01-02 | Canonical hash loop prevention | SATISFIED | `canonical_hash` + `NormalisedTask` implemented; 8 hash stability tests pass |
| LOOP-2 | 01-02 | Hash normalisation rules (TZ, priority int, stripped title, bool, no metadata) | SATISFIED | All normalisation rules applied; TZ/Unicode/CRLF/null stability tests pass |
| SYNC-2 | 01-02 | Priority mapping Highest→4 (not inverted) | SATISFIED | `ZOHO_TO_TODOIST` explicit dict + regression guard `test_highest_is_NOT_todoist_1` |
| SYNC-5 | 01-02 | `[zoho:ID]` footer format + ZOHO_ID_RE regex | SATISFIED | `FOOTER_RE`, `ZOHO_ID_RE`, `strip_footer` exported; 4 normalise tests cover footer |
| SYNC-7 | 01-02 | Description excluded from sync and canonical hash | SATISFIED | `NormalisedTask` has no `description` field; `test_label_not_in_hash` confirms via reflection |
| SYNC-9 | 01-02 | Labels excluded from canonical hash | SATISFIED | `NormalisedTask` has no `labels` field; `test_label_not_in_hash` confirms via reflection |
| OBS-5 | 01-02 | Structured logging, LOG_LEVEL configurable, graceful unknown-level fallback | SATISFIED | `configure_logging` with `getattr(logging, level.upper(), logging.INFO)` fallback; 8 tests |
| EDGE-4 | 01-01 | `ZOHO_TERMINAL_STATUSES` comma-separated, load+cache at startup | PARTIAL — config layer only | `zoho_terminal_statuses_list` property parses the env var; actual "cache at startup" usage deferred to Phase 4. REQUIREMENTS traceability maps EDGE-4 to Phase 4. |

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `app/db/models.py` | `Boolean` imported but unused (line 2) | Info | Unused import; no functional impact |

No stubs, TODO/FIXME comments, placeholder return values, or empty implementations found across any Phase 1 file.

### Human Verification Required

None. All must-haves are verifiable programmatically. The phase goal is pure foundation code (config, pure-function modules, schema, tests) with no user-visible UI, real-time behavior, or external service integration that requires live testing.

### Gaps Summary

No gaps. All 16 observable truths are verified against the actual codebase. 92 unit tests pass. The 4 items listed as deferred are explicitly partial implementations for requirements that span multiple phases — confirmed by the REQUIREMENTS.md traceability table showing Phase 1 + Phase 2 or Phase 5 for INFRA-1, INFRA-3, INFRA-6, and INFRA-7.

---

_Verified: 2026-04-23T18:30:00Z_
_Verifier: Claude (gsd-verifier)_
