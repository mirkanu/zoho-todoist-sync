---
phase: 01-foundation
fixed_at: 2026-04-23T00:00:00Z
review_path: .planning/phases/01-foundation/01-REVIEW.md
iteration: 1
fix_scope: critical_warning
findings_in_scope: 5
fixed: 5
skipped: 0
status: all_fixed
---

# Phase 01: Code Review Fix Report

**Fixed at:** 2026-04-23T00:00:00Z
**Source review:** .planning/phases/01-foundation/01-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 5
- Fixed: 5
- Skipped: 0

## Fixed Issues

### CR-01: Module-level `Settings()` instantiation fails hard on import

**Files modified:** `app/core/config.py`
**Commit:** f682790
**Applied fix:** Added `from functools import lru_cache`, introduced a `get_settings() -> Settings` function decorated with `@lru_cache(maxsize=1)`, and changed the module-level `settings = Settings()` to `settings = get_settings()`. The module-level alias is kept for backwards compatibility; callers can now patch settings in tests via `get_settings.cache_clear()` + `monkeypatch.setenv()`.

---

### WR-01: Silent data truncation in `normalise_due_date` fallback path

**Files modified:** `app/core/normalise.py`
**Commit:** 12752ea
**Applied fix:** Changed the `except ValueError` branch from `return raw[:10]` to `return None`, with a comment explaining the rationale. Unparseable date strings are now treated as "no due date" rather than producing a potentially invalid truncated string.

---

### WR-02: `configure_logging` called at module scope before app startup

**Files modified:** `app/main.py`
**Commit:** 72aed0b
**Applied fix:** Removed the top-level `configure_logging(settings.log_level)` call and moved it to the first statement inside the `lifespan` async context manager. The module-level `log = get_logger(__name__)` remains in place (structlog loggers are safe to create before `configure` is called).

---

### WR-03: `ZOHO_TO_TODOIST` dict type annotation incorrect

**Files modified:** `app/core/priority.py`
**Commit:** 5c5198f
**Applied fix:** Changed the dict type annotation from `dict[str, int]` to `dict[str | None, int]`, making it consistent with the `None` key that is explicitly present in the dict and with the `zoho_priority: str | None` parameter of `zoho_to_todoist_priority`.

---

### WR-04: `kv_store.updated_at` `onupdate` trigger absent from migration

**Files modified:** `app/db/migrations/versions/001_initial_schema.py`
**Commit:** 3327614
**Applied fix:** Appended two `op.execute()` calls at the end of `upgrade()` to create a `set_kv_store_updated_at` PL/pgSQL function and a `BEFORE UPDATE` trigger on `kv_store`. Added matching `DROP TRIGGER` / `DROP FUNCTION` statements at the start of `downgrade()` to keep the migration reversible.

---

## Skipped Issues

None — all in-scope findings were fixed.

---

_Fixed: 2026-04-23T00:00:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
