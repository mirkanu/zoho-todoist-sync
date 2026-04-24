---
phase: 03-todoist-read
plan: "02"
subsystem: todoist-client
tags: [todoist, http-client, auth, typed-exceptions, sync-api]
one_liner: "TodoistClient wrapping TodoistAPIAsync for REST fetches + direct httpx Sync API delta with client-side project filtering and typed exceptions"
dependency_graph:
  requires: []
  provides:
    - app.todoist.client.TodoistClient
    - app.todoist.client.TodoistAuthError
    - app.todoist.client.TodoistNotFoundError
    - app.todoist.client.TodoistRateLimitError
    - app.todoist.client.TodoistAPIError
  affects:
    - Phase 4 (write layer consumes TodoistClient)
    - Phase 7 (reconciliation consumes fetch_sync_delta)
tech_stack:
  added:
    - todoist-api-python (TodoistAPIAsync, async SDK)
    - httpx (direct async client for Sync API POST)
  patterns:
    - Typed exception hierarchy mirroring app.zoho.client
    - TDD RED/GREEN for both tasks
    - Client-side project_id filter on Sync API items
key_files:
  created:
    - app/todoist/__init__.py
    - app/todoist/client.py
    - tests/unit/test_todoist_client.py
  modified: []
decisions:
  - "Used TodoistAPIAsync (async SDK) not TodoistAPI (sync) — matches FastAPI/arq async runtime"
  - "Stored api_token as self._api_token rather than accessing private self._api._token — avoids SDK internals"
  - "fetch_sync_delta uses a fresh httpx.AsyncClient per call (async with) — simpler lifecycle, Sync API calls are infrequent"
  - "SYNC_API_URL constant defined at module level for testability and visibility"
metrics:
  duration_minutes: 8
  completed_date: "2026-04-24"
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 0
requirements_satisfied: [SYNC-5, SYNC-8]
---

# Phase 03 Plan 02: Todoist HTTP Client Summary

## What Was Built

`app/todoist/client.py` — async Todoist client with two paths:

1. **REST path** (`fetch_todoist_task`): wraps `TodoistAPIAsync.get_task()`, catches `httpx.HTTPStatusError` and maps status codes to 4 typed exceptions. Logs `todoist_fetch_task` event with task ID only (never token).

2. **Sync API path** (`fetch_sync_delta`): direct `httpx.AsyncClient` POST to `https://api.todoist.com/api/v1/sync` with `Authorization: Bearer {token}` and form data `sync_token + resource_types='["items"]'`. Returns `(items, new_sync_token)`. Filters items client-side by `project_id` when provided.

Both paths raise typed exceptions — `TodoistAuthError` propagates up to halt the worker (SYNC-5) rather than being swallowed.

## Commits

| Task | Description | Commit |
|------|-------------|--------|
| 1 | TodoistClient with typed exceptions and fetch_todoist_task | 2c87107 |
| 2 | fetch_sync_delta with client-side project filtering | 8827ffc |

## Test Coverage

12 tests total (`tests/unit/test_todoist_client.py`):

| Path | Tests |
|------|-------|
| REST 200 success | 1 |
| REST 401/404/429/500 → typed exceptions | 4 |
| Sync API full sync (*) | 1 |
| Sync API project_id filter (client-side) | 1 |
| Sync API no filter (all items returned) | 1 |
| Sync API 401/429/500 → typed exceptions | 3 |
| Sync API Bearer auth header verified | 1 |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Task model missing `updated_at` field in test mock**
- **Found during:** Task 1 GREEN — `test_fetch_task_success` failed with `dataclass_wizard.errors.MissingFields`
- **Issue:** The `Task` dataclass in `todoist-api-python` v4 requires `updated_at` but the plan's mock JSON did not include it
- **Fix:** Added `"updated_at": "2026-04-01T00:00:00Z"` to the success test mock response
- **Files modified:** `tests/unit/test_todoist_client.py`
- **Commit:** 2c87107

**2. [Note] TDD RED for Task 2 not separately committed**
- `fetch_sync_delta` was included in the initial `client.py` write during Task 1 GREEN (the plan provided the full implementation in Task 1's action block). As a result, Task 2 new tests passed immediately without a separate failing RED commit. Production code and tests are both correct.

## Threat Mitigation Verified

| Threat | Status |
|--------|--------|
| T-03-02-01: API token in logs | Mitigated — no `log.*` call references `self._api_token`; grep confirms |
| T-03-02-02: Items from other projects processed | Mitigated — client-side filter in `fetch_sync_delta`; test asserts |
| T-03-02-03: 401 silently retried | Mitigated — `TodoistAuthError` raised, not caught inside client |
| T-03-02-04: Missing keys in Sync API response | Mitigated — `body.get("items", []) or []`; `body["sync_token"]` loud-fails on protocol violation |
| T-03-02-05: TLS not enforced | Accepted — httpx defaults to `verify=True` |

## Known Stubs

None.

## Threat Flags

None — no new network endpoints or auth paths beyond what the plan specified.

## Self-Check: PASSED

- [x] `app/todoist/__init__.py` exists
- [x] `app/todoist/client.py` exists and contains `class TodoistClient`
- [x] `tests/unit/test_todoist_client.py` exists with 12 test functions
- [x] Commits `2c87107` and `8827ffc` exist in git log
- [x] `pytest tests/unit/test_todoist_client.py -x -q` → 12 passed
- [x] `pytest tests/ -q` → 136 passed (full suite green)
