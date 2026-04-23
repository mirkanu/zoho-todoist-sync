---
phase: 01-foundation
reviewed: 2026-04-23T00:00:00Z
depth: standard
files_reviewed: 31
files_reviewed_list:
  - alembic.ini
  - app/core/config.py
  - app/core/hash.py
  - app/core/__init__.py
  - app/core/logging.py
  - app/core/normalise.py
  - app/core/priority.py
  - app/db/__init__.py
  - app/db/migrations/env.py
  - app/db/migrations/__init__.py
  - app/db/migrations/script.py.mako
  - app/db/migrations/versions/001_initial_schema.py
  - app/db/migrations/versions/__init__.py
  - app/db/models.py
  - app/__init__.py
  - app/main.py
  - .env.example
  - .gitignore
  - pyproject.toml
  - .python-version
  - requirements-dev.txt
  - requirements.txt
  - tests/conftest.py
  - tests/__init__.py
  - tests/unit/__init__.py
  - tests/unit/test_config.py
  - tests/unit/test_hash.py
  - tests/unit/test_logging.py
  - tests/unit/test_migration_and_app.py
  - tests/unit/test_models.py
  - tests/unit/test_normalise.py
  - tests/unit/test_priority.py
findings:
  critical: 1
  warning: 4
  info: 3
  total: 8
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-04-23T00:00:00Z
**Depth:** standard
**Files Reviewed:** 31
**Status:** issues_found

## Summary

The foundation layer is solid overall. The pure-logic modules (`hash`, `normalise`, `priority`) are correct, well-tested, and consistent with the documented constraints in CLAUDE.md (priority mapping, date-only normalisation, NFC normalisation). The database schema, Alembic migration, and FastAPI stub are structurally sound.

There is one critical issue: `settings = Settings()` executes at module import time (`app/core/config.py` line 28), which causes every test that imports any `app.*` module to require all production environment variables to be present — even tests that do not exercise config at all. Currently this is papered over by `test_main_py_importable_with_env` setting `os.environ` directly and purging `sys.modules`, but it is fragile and leaks env state between tests.

Four warnings cover an unsafe fallback in date normalisation, an unguarded `configure_logging` call at module scope, a type-safety gap in the priority dict, and a missing `onupdate` trigger in the migration. Three info items flag an unpinned `structlog` dependency, a `requires-python` version mismatch, and a minor test isolation issue.

---

## Critical Issues

### CR-01: Module-level `Settings()` instantiation fails hard on import — leaks into unrelated tests

**File:** `app/core/config.py:28`

**Issue:** `settings = Settings()` runs unconditionally when the module is first imported. Any test file that transitively imports `app.core.config` (or any module that imports it, such as `app.main`, `app.db.migrations.env`) will raise a `pydantic_settings.ValidationError` unless every required env var is set. `test_main_py_importable_with_env` (line 127–149) already works around this by setting `os.environ` directly before import and purging `sys.modules`, but this approach is order-sensitive: if another test in the same session has already imported `app.core.config` into the module cache, the reload has no effect. The `test_alembic_ini_*` tests import the migration `env.py` structurally (via `importlib.util`), but `env.py` itself imports `settings` at the top level, creating the same risk.

**Fix:** Guard the eager instantiation behind a function or use `lru_cache`, and expose a `get_settings()` accessor alongside the module-level singleton:

```python
# app/core/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ...  # fields unchanged
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Keep the module-level alias for backwards compatibility,
# but callers should prefer get_settings() so tests can patch it.
settings = get_settings()
```

In tests, override via:
```python
from app.core import config as config_mod
config_mod.get_settings.cache_clear()
monkeypatch.setenv(...)
config_mod.settings = config_mod.get_settings()
```

This is the standard FastAPI pattern for testable settings and eliminates the fragile `sys.modules` purge.

---

## Warnings

### WR-01: Silent data truncation in `normalise_due_date` fallback path

**File:** `app/core/normalise.py:28-29`

**Issue:** When `datetime.fromisoformat(raw)` raises `ValueError`, the function falls back to `raw[:10]` without validating the result. For an input like `"tomorrow"`, `"not-a-date"`, or a non-ISO Zoho date format, this silently produces an invalid date string (e.g., `"tomorro"`, `"not-a-dat"`) that will propagate into the canonical hash and be stored in the database, causing silent sync mismatches. The CLAUDE.md open question #3 explicitly flags uncertainty about Zoho's raw `Due_Date` format.

**Fix:** Return `None` (treating unparseable input as "no due date") rather than truncating blindly:

```python
def normalise_due_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(datetime.fromisoformat(raw).date())
    except ValueError:
        # Log the unexpected format before discarding it.
        return None
```

If the date value must be preserved in some form, validate the 10-char slice matches `YYYY-MM-DD` with a regex before returning it.

---

### WR-02: `configure_logging` called at module scope before app startup — silently overrides test logging

**File:** `app/main.py:6`

**Issue:** `configure_logging(settings.log_level)` executes at import time (line 6), before FastAPI's `lifespan` runs. `structlog.configure()` is a global, process-wide side-effect. Every test that imports `app.main` (including `test_main_py_importable_with_env`, which does so explicitly) will reconfigure structlog for the entire test process, potentially swallowing or reformatting log output from other tests. This is also why the test imports `app.main` last after setting env vars — the ordering dependency is implicit.

**Fix:** Move `configure_logging` into the `lifespan` context manager, which is the correct FastAPI startup hook:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    log.info(
        "startup",
        zoho_region=settings.zoho_region,
        todoist_task_id_field=settings.zoho_todoist_task_id_field or "NOT_SET",
        log_level=settings.log_level,
    )
    yield
    log.info("shutdown")
```

The module-level `log = get_logger(__name__)` can remain — structlog loggers are safe to create before `configure` is called, they just pick up the configuration at emit time.

---

### WR-03: `ZOHO_TO_TODOIST` dict uses `None` as a key — type annotation is incorrect and `.get()` may shadow `None` input

**File:** `app/core/priority.py:4-12`

**Issue:** The dict is typed as `dict[str, int]` but contains `None` as a key (line 10) and `""` as another key (line 11). The annotation `dict[str, int]` is wrong — it should be `dict[str | None, int]`. This matters because `zoho_to_todoist_priority` uses `.get(zoho_priority, 1)` — if a future type-checker or linter enforces the annotation, calls with `None` may be flagged as unreachable. More importantly, at runtime `.get(None, 1)` works because `None` is hashable, but the explicit `None` key is redundant with the fallback default of `1`. The real risk is if code elsewhere passes `None` and relies on the explicit key being present (as `test_zoho_to_todoist_dict_contains_none_key` explicitly asserts), while the annotation says this is not possible.

**Fix:** Correct the type annotation:

```python
ZOHO_TO_TODOIST: dict[str | None, int] = {
    "Highest": 4,
    "High":    3,
    "Normal":  2,
    "Low":     1,
    "Lowest":  1,
    None:      1,
    "":        1,
}
```

The `zoho_to_todoist_priority` signature should also be `(zoho_priority: str | None) -> int`, which it already is — this makes the dict annotation consistent with the function signature.

---

### WR-04: `kv_store.updated_at` `onupdate` trigger is in the ORM model but absent from the Alembic migration

**File:** `app/db/models.py:47-48` and `app/db/migrations/versions/001_initial_schema.py:47-52`

**Issue:** The `KVStore` ORM model sets `onupdate=func.now()` on `updated_at` (line 48 of `models.py`). This is a SQLAlchemy ORM-level hook that fires when you call `session.flush()` on a tracked instance. However, the corresponding `CREATE TABLE` in the migration (lines 47–52 of the migration file) does not include a `ON UPDATE` trigger at the database level — there is no equivalent `CREATE OR REPLACE FUNCTION` / `CREATE TRIGGER` DDL. For any update that bypasses the ORM (raw SQL, bulk updates, Alembic data migrations), `updated_at` will not be refreshed. Since `kv_store` is intended to track the Zoho `Modified_Time` cursor and OAuth token state, stale timestamps could cause incorrect delta-sync windows.

**Fix:** Either accept the ORM-only semantics (and document that raw SQL must manually update `updated_at`), or add a database-level trigger in the migration:

```sql
-- In a new migration or appended to upgrade():
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER kv_store_set_updated_at
BEFORE UPDATE ON kv_store
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

Alternatively, use Alembic's `op.execute()` to run the DDL in the existing migration before it is applied to production.

---

## Info

### IN-01: `structlog` is unpinned in `requirements.txt`

**File:** `requirements.txt:9`

**Issue:** All other dependencies are pinned to exact versions (`pydantic-settings==2.14.0`, `fastapi==0.136.0`, etc.), but `structlog` has no version constraint. A future `pip install` or Railway build could pull a breaking `structlog` release. Given that `configure_logging` calls `structlog.contextvars.merge_contextvars` (which was renamed in structlog 21.2.0), an older version could cause a silent `AttributeError` at startup.

**Fix:** Pin structlog to the currently installed version:

```
structlog==25.1.0
```

(Verify the exact installed version with `pip show structlog` and use that.)

---

### IN-02: `pyproject.toml` requires Python `>=3.11` but `.python-version` specifies `3.12`

**File:** `pyproject.toml:5` and `.python-version`

**Issue:** `requires-python = ">=3.11"` is broader than the runtime actually used. The comment in `app/core/normalise.py` line 26 explicitly notes `datetime.fromisoformat()` handles tz-offset correctly "on Python 3.11+", so 3.11 is a reasonable floor. However, the mismatch means a developer on 3.11 could install and run the project, encounter different behaviour from CI/production (which pins 3.12), and submit bugs that are not reproducible. It is also inconsistent — CLAUDE.md states "Python 3.12".

**Fix:** Align the lower bound with the actual runtime:

```toml
requires-python = ">=3.12"
```

---

### IN-03: `test_main_py_importable_with_env` sets env vars on `os.environ` directly instead of using `monkeypatch`

**File:** `tests/unit/test_migration_and_app.py:141-143`

**Issue:** The test sets `os.environ[k] = v` directly (lines 141–143) without using pytest's `monkeypatch` fixture. These env vars are never cleaned up: they persist in the process environment for all subsequently-run tests in the same session. If test ordering puts this test before `test_settings_raises_on_missing_required_var` (which deliberately strips these vars), the subprocess approach in that test is the only reason it still passes. Any future test that relies on a clean env could be affected.

**Fix:** Accept `monkeypatch` as a parameter and use it for all env mutations:

```python
def test_main_py_importable_with_env(monkeypatch):
    env_vars = { ... }
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    # sys.modules purge remains acceptable here
    ...
```

---

_Reviewed: 2026-04-23T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
