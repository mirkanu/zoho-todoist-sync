---
phase: 02-zoho-read
plan: "01"
subsystem: zoho-client
tags: [zoho, http-client, normalisation, tdd, unit-tests]
one_liner: "Async ZohoClient (EU v8) with 4 typed exceptions, field metadata resolution, paginated search, and zoho_record_to_normalised adapter reusing Phase 1 helpers"
dependency_graph:
  requires: [app/core/normalise.py, app/core/priority.py, app/core/logging.py]
  provides: [app/zoho/client.py, app/zoho/normalise.py]
  affects: [Phase 03+ sync worker, token refresh loop (Plan 02-02)]
tech_stack:
  added: [pytest-httpx>=0.35.0]
  patterns: [httpx.AsyncClient per-call context manager, typed exception hierarchy, TDD RED/GREEN]
key_files:
  created:
    - app/zoho/__init__.py
    - app/zoho/client.py
    - app/zoho/normalise.py
    - tests/unit/test_zoho_client.py
    - tests/unit/test_zoho_normalise.py
  modified:
    - requirements-dev.txt
decisions:
  - "Per-call httpx.AsyncClient (not shared instance): ZohoClient is a dataclass with mutable access_token; a shared client would cache the old token after refresh. Per-call context managers are simpler and correct for this use case."
  - "v8 endpoint path (not v6): Plan specifies /crm/v8 matching the research doc; Zoho EU base is www.zohoapis.eu/crm/v8."
  - "Custom field discovery via field_label (not display_value or api_name scan): Pitfall 6 from RESEARCH.md — api_name may vary per org; field_label 'Todoist Task ID' is stable."
  - "204 from search treated as valid empty list (not exception): Zoho returns 204 No Content for empty search results; _handle would wrongly raise ZohoAPIError on 204."
metrics:
  duration_seconds: 502
  completed_date: "2026-04-23"
  tasks_completed: 3
  files_created: 5
  files_modified: 1
  tests_added: 16
  total_unit_tests: 108
---

# Phase 2 Plan 1: ZohoClient HTTP Layer Summary

Async Zoho CRM v8 EU client with typed exceptions, field metadata resolution, paginated modified-since search, and a normalisation adapter — all covered by 16 new unit tests via pytest-httpx mocks. No live credentials needed.

## Files Created

| File | Purpose |
|------|---------|
| `app/zoho/__init__.py` | Python package marker (empty) |
| `app/zoho/client.py` | ZohoClient dataclass + 4 typed exceptions + 3 async methods |
| `app/zoho/normalise.py` | zoho_record_to_normalised adapter |
| `tests/unit/test_zoho_client.py` | 11 async tests for ZohoClient |
| `tests/unit/test_zoho_normalise.py` | 5 sync tests for zoho_record_to_normalised |

## Exceptions Defined

| Class | Trigger |
|-------|---------|
| `ZohoAuthError` | HTTP 401 — stop and alert, do not retry |
| `ZohoNotFoundError` | HTTP 404 — task or resource absent |
| `ZohoRateLimitError` | HTTP 429 — retry with backoff |
| `ZohoAPIError` | All other non-2xx — wraps status code + 200-char body excerpt |

## Methods Implemented

| Method | Description |
|--------|-------------|
| `get_task(id)` | GET /Tasks/{id}, returns full response dict |
| `get_fields_metadata(module)` | GET /settings/fields?module=..., resolves todoist_task_id_api_name and status_picklist_values |
| `fetch_tasks_modified_since(since, owner_id)` | Paginated GET /Tasks/search with Modified_Time + Owner criteria, handles 204 |

## Test Count

- 11 async tests in `test_zoho_client.py` (httpx_mock fixtures)
- 5 sync tests in `test_zoho_normalise.py`
- Full unit suite: **108 tests pass** (Phase 1 + Phase 2 Plan 01, no regressions)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed pytest-httpx url__startswith matcher not supported in v0.36.2**
- **Found during:** Task 2 test execution
- **Issue:** Plan's test stubs used `url__startswith=` kwarg which does not exist in pytest-httpx 0.36.2 (only `url`, `method`, `match_params`, etc. are valid). TypeError on test collection.
- **Fix:** Replaced `url__startswith=f"{ZOHO_BASE}/Tasks/search"` with `url=re.compile(rf"{re.escape(ZOHO_BASE)}/Tasks/search.*")` in 3 tests. Added `import re` to test file.
- **Files modified:** `tests/unit/test_zoho_client.py`
- **Commit:** 207bb76

## Threat Surface Scan

All mitigations from the plan's threat model were applied:

| Threat | Status |
|--------|--------|
| T-02-01: access_token never logged | Confirmed — only log event is `zoho_field_resolved` which logs `todoist_task_id_api_name` and `status_picklist_values`, not the token |
| T-02-02: _handle error message truncated to 200 chars | Confirmed — `resp.text[:200]` |
| T-02-03: defensive .get() on all response fields | Confirmed — `body.get("fields", [])`, `body.get("data", [])`, `body.get("info") or {}` |
| T-02-04: 401 raises ZohoAuthError, not retried in client | Confirmed — caller decides retry policy |
| T-02-05: pagination terminates on more_records==False or 204 | Confirmed |
| T-02-06: httpx TLS verification default (no verify=False) | Confirmed |
| T-02-07: context string in every exception | Confirmed |

No new threat surface introduced beyond the plan's scope.

## Self-Check: PASSED

All created files exist on disk. All task commits verified in git log:
- c5a539d — Task 1 TDD RED scaffold
- 207bb76 — Task 2 TDD GREEN ZohoClient
- ca1d30a — Task 3 TDD GREEN normalise.py
