---
status: complete
phase: 02-zoho-read
source: [02-01-SUMMARY.md, 02-02-SUMMARY.md]
started: 2026-04-23T00:00:00Z
updated: 2026-04-23T00:00:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: Kill any running server/service. Start the application from scratch (or run `pytest` from a clean state). All 124 unit tests pass with no errors — server boots and test suite reports 0 failures, 0 errors.
result: pass

### 2. ZohoClient Typed Exceptions
expected: Running `pytest tests/unit/test_zoho_client.py -v` shows tests covering all four exception types pass — ZohoAuthError on 401, ZohoNotFoundError on 404, ZohoRateLimitError on 429, ZohoAPIError on other non-2xx. Error messages include a 200-char body excerpt.
result: pass

### 3. Paginated Task Fetch (204 + Multi-Page)
expected: Tests for `fetch_tasks_modified_since` pass — 204 response returns an empty list (no exception), paginated responses accumulate all records across pages and stop when `more_records=False`.
result: pass

### 4. Field Metadata Resolution
expected: `get_fields_metadata` test passes — resolves `todoist_task_id_api_name` by matching field_label "Todoist Task ID" (not by api_name scan), and `status_picklist_values` is a list of string values from the picklist.
result: pass

### 5. Normalisation Adapter
expected: All 5 tests in `test_zoho_normalise.py` pass — `zoho_record_to_normalised` maps Zoho fields to the normalised task schema using Phase 1 helpers (priority mapping, date normalisation).
result: pass

### 6. Token Persistence (Atomic upsert_kv)
expected: Token manager tests pass — `upsert_kv` writes key/value without committing internally; callers commit once after both writes. `load_token_from_kv` returns None for missing/corrupt data and triggers a refresh.
result: pass

### 7. Proactive Refresh Loop
expected: `test_proactive_refresh_loop_*` tests pass — loop refreshes every 50 minutes, re-raises on failure (INFRA-6), and `token_state` is unchanged after a failed refresh.
result: pass

### 8. FastAPI Lifespan Startup Sequence
expected: All 7 tests in `test_main_lifespan.py` pass — lifespan populates `token_state` and `zoho_field_cache`, spawns the background refresh task, emits `zoho_todoist_task_id_field_not_found` WARN when api_name missing, and `zoho_terminal_status_not_in_picklist` WARN for each unknown configured terminal status.
result: pass

## Summary

total: 8
passed: 8
issues: 0
pending: 0
skipped: 0

## Gaps

[none]
