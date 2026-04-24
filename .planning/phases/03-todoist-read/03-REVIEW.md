---
phase: 03-todoist-read
reviewed: 2026-04-24T00:00:00Z
depth: standard
files_reviewed: 10
files_reviewed_list:
  - app/main.py
  - app/todoist/client.py
  - app/todoist/__init__.py
  - app/todoist/normalise.py
  - app/todoist/sync_manager.py
  - tests/unit/test_main_lifespan.py
  - tests/unit/test_migration_and_app.py
  - tests/unit/test_todoist_client.py
  - tests/unit/test_todoist_normalise.py
  - tests/unit/test_todoist_sync_manager.py
findings:
  critical: 0
  warning: 2
  info: 3
  total: 5
status: issues_found
---

# Phase 03: Code Review Report

**Reviewed:** 2026-04-24
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

Ten files were reviewed covering the Todoist read layer: the HTTP client, normalisation helpers, startup sync orchestration, the FastAPI lifespan, and all unit tests. The implementation is well-structured and the test suite is thorough, with correct handling of the full-sync vs. incremental-sync decision, token-before-processing ordering, and footer-based item filtering.

Two warnings require attention before Phase 5 consumes this layer:

1. `_raise_typed` is annotated `-> None` instead of `-> NoReturn`, creating a latent unbound-variable defect in `fetch_todoist_task`.
2. `fetch_sync_delta` opens a new `httpx.AsyncClient` on every call, forgoing connection reuse.

Three info items are noted (redundant markers, orphan constant, test duplication) but do not affect correctness.

---

## Warnings

### WR-01: `_raise_typed` annotated `-> None` causes latent unbound variable in `fetch_todoist_task`

**File:** `app/todoist/client.py:55-57`

**Issue:** `_raise_typed` always raises one of the four typed exceptions — it never returns. However it is declared `-> None`. Python's type checker (mypy/pyright) therefore does NOT treat the `except` branch as an unconditional re-raise, which means the identifier `task` is considered potentially unbound at line 57 (`return task`). If a future refactor of `_raise_typed` accidentally introduced a non-raising code path, callers would get an `UnboundLocalError` at runtime. The correct annotation is `-> NoReturn`.

**Fix:**
```python
from typing import NoReturn

@staticmethod
def _raise_typed(status: int, context: str, cause: Exception) -> NoReturn:
    if status == 401:
        raise TodoistAuthError(f"401 Unauthorized — {context}") from cause
    if status == 404:
        raise TodoistNotFoundError(f"404 Not Found — {context}") from cause
    if status == 429:
        raise TodoistRateLimitError(f"429 Rate limit — {context}") from cause
    raise TodoistAPIError(f"{status} — {context}") from cause
```

With `-> NoReturn`, the type checker understands the `except` block is a dead-end and correctly narrows `task` to always-bound at line 57.

---

### WR-02: `fetch_sync_delta` creates a new `httpx.AsyncClient` on every call

**File:** `app/todoist/client.py:83`

**Issue:** Every call to `fetch_sync_delta` constructs and tears down a fresh `httpx.AsyncClient` inside the method body (`async with httpx.AsyncClient() as client: ...`). This means each Sync API request opens a new TCP connection with a full TLS handshake, discarding any connection pool. The REST path (`fetch_todoist_task`) reuses the persistent client managed by `TodoistAPIAsync`, so there is an inconsistency. While the current call rate (startup + 15-minute polls) keeps this from being a hot path, it is architecturally inconsistent and will become wasteful if the poll interval tightens.

**Fix:** Store a shared `httpx.AsyncClient` at construction time and close it in `close()`:

```python
def __init__(self, api_token: str) -> None:
    self._api_token = api_token
    self._api = TodoistAPIAsync(token=api_token)
    self._http = httpx.AsyncClient()   # shared, long-lived

async def close(self) -> None:
    await self._api.close()
    await self._http.aclose()

async def fetch_sync_delta(self, sync_token: str, project_id: str | None = None) -> ...:
    resp = await self._http.post(
        SYNC_API_URL,
        headers={"Authorization": f"Bearer {self._api_token}"},
        data={"sync_token": sync_token, "resource_types": '["items"]'},
    )
    ...
```

---

## Info

### IN-01: Redundant `@pytest.mark.asyncio` markers in two tests

**File:** `tests/unit/test_main_lifespan.py:167,236`

**Issue:** `pytest.ini` sets `asyncio_mode = "auto"`, which automatically treats all `async def` test functions as async. The explicit `@pytest.mark.asyncio` decorators on `test_lifespan_initialises_todoist_client_and_runs_startup_sync` (line 167) and `test_lifespan_startup_sync_failure_propagates` (line 236) are redundant. All other async tests in the same file omit the marker correctly.

**Fix:** Remove the two `@pytest.mark.asyncio` decorators. No behaviour changes.

---

### IN-02: `FULL_SYNC_SENTINEL` constant is not exported but is tested indirectly

**File:** `app/todoist/sync_manager.py:27`

**Issue:** `FULL_SYNC_SENTINEL = "*"` is a module-level constant used both inside `load_sync_token` and referenced in the `full_sync=` log field of `startup_sync`. The test suite checks the wildcard sentinel only via observable side-effects (what token is passed to `fetch_sync_delta`), never importing the constant directly. This is fine for current coverage but worth noting if the sentinel value ever changes — the constant name rather than the string literal should be the single source of truth in test assertions.

**Fix:** In `test_todoist_sync_manager.py`, import and reference `FULL_SYNC_SENTINEL` instead of the bare string `"*"` in assertions:

```python
from app.todoist.sync_manager import FULL_SYNC_SENTINEL, ...

todoist_client.fetch_sync_delta.assert_awaited_once_with(
    sync_token=FULL_SYNC_SENTINEL, project_id="6gCPcWwM392GhXQh"
)
```

---

### IN-03: `app/todoist/__init__.py` is empty

**File:** `app/todoist/__init__.py:1`

**Issue:** The file is completely empty (zero bytes). This is valid Python, but if `TodoistClient` or normalisation helpers are imported frequently via `app.todoist.client` / `app.todoist.normalise`, a re-export in `__init__.py` would reduce import path verbosity. Not a defect — purely informational.

**Fix:** No action required unless the team wants to add explicit re-exports once Phase 5 stabilises the public surface.

---

_Reviewed: 2026-04-24_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
