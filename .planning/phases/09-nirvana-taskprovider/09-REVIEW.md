---
phase: 09-nirvana-taskprovider
reviewed: 2026-07-28T00:00:00Z
depth: standard
files_reviewed: 31
files_reviewed_list:
  - app/core/config.py
  - app/core/priority.py
  - app/db/migrations/versions/002_add_provider_column.py
  - app/db/models.py
  - app/main.py
  - app/nirvana/__init__.py
  - app/nirvana/client.py
  - app/nirvana/normalise.py
  - app/nirvana/writer.py
  - app/providers/__init__.py
  - app/providers/base.py
  - app/todoist/client.py
  - app/webhooks/router.py
  - app/worker/jobs.py
  - app/worker/reconciler.py
  - app/worker/settings.py
  - scripts/migrate.py
  - tests/conftest.py
  - tests/unit/test_config.py
  - tests/unit/test_main_lifespan.py
  - tests/unit/test_migration.py
  - tests/unit/test_migration_and_app.py
  - tests/unit/test_models.py
  - tests/unit/test_nirvana_client.py
  - tests/unit/test_nirvana_normalise.py
  - tests/unit/test_nirvana_writer.py
  - tests/unit/test_priority.py
  - tests/unit/test_provider_factory.py
  - tests/unit/test_reconciler.py
  - tests/unit/test_todoist_client.py
  - tests/unit/test_webhooks.py
  - tests/unit/test_worker_jobs.py
  - tests/unit/test_worker_settings.py
findings:
  critical: 1
  warning: 3
  info: 2
  total: 6
status: issues_found
---

# Phase 09: Code Review Report

**Reviewed:** 2026-07-28T00:00:00Z
**Depth:** standard
**Files Reviewed:** 31
**Status:** issues_found

## Summary

Reviewed the Nirvana TaskProvider integration (provider abstraction, Nirvana client/normaliser/writer, Todoist client, worker jobs/reconciler/settings, webhook router, migration 002, and their test suites).

The provider-abstraction refactor (`TaskProvider` Protocol, `get_provider()` factory, `sync_task`/`orphan_sweep` moving from `todoist_client`/`TodoistNotFoundError`-only to a generic `task_provider`) is generally well done and covered by tests. However, the refactor left `app/worker/settings.py`'s `on_startup` no longer constructing a raw `TodoistClient` and storing it in `ctx["todoist_client"]` — it only populates `ctx["task_provider"]` now. Two cron jobs that were NOT migrated to the generic provider (`reconcile_sweep` and `daily_summary`, both of which still hard-depend on a raw `TodoistClient` for Sync-API delta fetch / `_api.add_task`) will crash with `KeyError` on every real run in production, **regardless of which `TASK_PROVIDER` is configured**. This is a BLOCKER: it breaks reconciliation and the daily summary task unconditionally. The unit tests for these code paths mask the bug because they manually construct `ctx` with a `"todoist_client"` key instead of exercising the real `on_startup` wiring.

A handful of lower-severity issues are noted below (an empty-list truthiness bug in the Nirvana client, and some minor doc/test coverage gaps).

## Critical Issues

### CR-01: `ctx["todoist_client"]` is never populated by `on_startup` — `reconcile_sweep` and `daily_summary` crash on every run

**File:** `app/worker/settings.py:92-94`
**Issue:** `on_startup` was refactored to build the provider-agnostic `task_provider` (`ctx["task_provider"] = get_provider(settings)`) but no longer constructs a `TodoistClient` or stores it under `ctx["todoist_client"]`. However, two registered cron jobs still directly require `ctx["todoist_client"]`:

- `app/worker/reconciler.py:79` — `reconcile_sweep`: `todoist_client = ctx["todoist_client"]` (used for `fetch_sync_delta`, the Todoist Sync-API incremental delta). This cron runs every 15 minutes (`WorkerSettings.cron_jobs`, `minute={0, 15, 30, 45}`).
- `app/worker/daily_summary.py:25` — `daily_summary`: `todoist_client = ctx["todoist_client"]` (used for `_api.add_task` / `_api.complete_task`). This cron runs daily at midnight UTC.

Since `ctx` never contains the `"todoist_client"` key, both jobs raise `KeyError` immediately on every scheduled invocation — this is unconditional (it happens even when `TASK_PROVIDER=todoist`, since `on_startup` simply never sets this key any more). This silently breaks reconciliation drift-detection and the daily summary/cleanup task in production.

The existing unit tests (`tests/unit/test_reconciler.py::_make_reconciler_ctx`, `tests/unit/test_worker_settings.py::test_on_startup_populates_ctx`) do not catch this: the reconciler tests hand-build a `ctx` dict that includes `"todoist_client": AsyncMock()` directly, bypassing the real `on_startup` wiring, and `test_on_startup_populates_ctx`'s docstring claims `on_startup` "must populate ctx with … todoist_client …" but the assertions never actually check for that key.

**Fix:** Restore construction of a `TodoistClient` in `on_startup` (independent of the active `TASK_PROVIDER`, since Todoist Sync-API delta + the summary task are Todoist-specific infra, not swappable via `TaskProvider`), e.g.:
```python
# app/worker/settings.py, inside on_startup, near ctx["task_provider"] = get_provider(settings)
from app.todoist.client import TodoistClient
ctx["todoist_client"] = TodoistClient(api_token=settings.todoist_api_token)
```
And close it in `on_shutdown` alongside `task_provider`:
```python
todoist_client = ctx.get("todoist_client")
if todoist_client is not None:
    await todoist_client.close()
```
Also update `test_on_startup_populates_ctx` to assert `"todoist_client" in ctx` (matching its own docstring) so this regression is caught by CI going forward.

## Warnings

### WR-01: `NirvanaClient.call_tool` collapses a legitimately empty list result into `{}`

**File:** `app/nirvana/client.py:66`
**Issue:** `return body.get("result") or {}` uses `or`, so if the Nirvana MCP tool legitimately returns an empty list (`"result": []`, e.g. `get_tags()` with zero tags configured, or a raw `get_tasks` call returning zero matches), the falsy `[]` is silently replaced with `{}`. Downstream, `get_tasks()` happens to still produce `[]` by accident (via `result.get("tasks", [])` on the now-dict `{}`), but `get_tags()` returns the raw `call_tool` result directly and would return `{}` instead of `[]` to its caller, which is a type-inconsistent contract violation (declared return type `Any`, callers/tests currently only exercise the non-empty case).
**Fix:** Use an explicit `is None` check instead of truthiness:
```python
result = body.get("result")
return result if result is not None else {}
```

### WR-02: `test_on_startup_populates_ctx` docstring/assertions mismatch masks CR-01

**File:** `tests/unit/test_worker_settings.py:88-129`
**Issue:** The test's docstring states `on_startup` "must populate ctx with session_factory, engine, zoho_client, todoist_client, _refresh_task" but the actual assertions never check for `"todoist_client" in ctx`. This test gap is what allowed CR-01 to ship undetected.
**Fix:** Add `assert "todoist_client" in ctx` (after implementing CR-01's fix) so the test docstring's claim is actually enforced.

### WR-03: `TodoistClient.fetch_todoist_task` / `fetch_sync_delta` only catch `httpx.HTTPStatusError`, not connection/timeout errors

**File:** `app/todoist/client.py:51-58`, `70-109`
**Issue:** Both methods only translate `httpx.HTTPStatusError` into the typed exception hierarchy (`TodoistAuthError`/`TodoistNotFoundError`/etc). A raw `httpx.ConnectError`, `httpx.ReadTimeout`, etc. (e.g. transient network blip) is not caught and will propagate as an untyped exception. In `app/worker/jobs.py`'s `sync_task`, the outer exception handling only catches the typed exceptions (`ZohoAuthError`, `ZohoRateLimitError`/`ZohoAPIError`/`TodoistRateLimitError`/`TodoistAPIError`/`NirvanaRateLimitError`/`NirvanaAPIError`, `ZohoNotFoundError`, `TodoistNotFoundError`/`NirvanaNotFoundError`) — an untyped httpx transport error would propagate out of `sync_task` uncaught, bypassing the Retry/backoff logic and the Redis lock release still happens via `finally`, but the job would be marked failed by arq without the intended retry-with-backoff semantics.
**Fix:** Wrap the `await self._api.get_task(...)` / `client.post(...)` calls with a broader `except httpx.HTTPError` (or explicitly catch `httpx.TransportError`) and raise `TodoistAPIError` so these transient errors flow through the existing retry path.

## Info

### IN-01: `_ZOHO_CHANNEL_ID = "1"` magic value with no explanation of uniqueness guarantee

**File:** `app/worker/reconciler.py:34`
**Issue:** The channel ID is hardcoded to the string `"1"`. This is fine for a single-project/single-tenant setup, but there's no comment explaining why `"1"` specifically was chosen or what would need to change if a second Zoho webhook channel were ever needed.
**Fix:** A one-line comment noting this is safe because only one webhook channel exists per Zoho org/module for this service would aid future maintainers.

### IN-02: `_NIRVANA_STATE_NEXT_EQUIVALENT` / `_NIRVANA_STATE_SCHEDULED_EQUIVALENT` singleton/pair sets add indirection for little benefit

**File:** `app/core/priority.py:41-43`
**Issue:** `_NIRVANA_STATE_NEXT_EQUIVALENT = {"next"}` is a single-element set used only for an `in` check; `_NIRVANA_STATE_SCHEDULED_EQUIVALENT` similarly wraps two literal strings. This is harmless but adds a layer of indirection over a simple string/tuple comparison.
**Fix:** Non-blocking; could simplify to `state == "next"` and `state in ("scheduled", "waiting")` directly, but current form is also fine if kept for readability/extensibility.

---

_Reviewed: 2026-07-28T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
