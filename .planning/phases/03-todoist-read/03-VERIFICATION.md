---
phase: 03-todoist-read
verified: 2026-04-24T00:00:00Z
status: passed
score: 9/9
overrides_applied: 0
re_verification: false
---

# Phase 3: Todoist Read — Verification Report

**Phase Goal:** Build the Todoist read layer — fetch tasks by ID (REST) and ingest incremental deltas (Sync API), normalise to NormalisedTask, extract [zoho:ID] footers, and wire a startup sync into the FastAPI lifespan with token persistence.
**Verified:** 2026-04-24T00:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC-1 | `fetch_todoist_task` returns a typed Task on 200 and raises typed exceptions (TodoistAuthError/NotFoundError/RateLimitError/APIError) on 401/404/429/5xx; auth failures stop sync and alert rather than silently retry | VERIFIED | `app/todoist/client.py` lines 50–67: `fetch_todoist_task` catches `httpx.HTTPStatusError`, maps 401→TodoistAuthError, 404→TodoistNotFoundError, 429→TodoistRateLimitError, other→TodoistAPIError. 5 tests in `test_todoist_client.py` cover all paths. Auth error is not caught internally — propagates to caller. |
| SC-2 | On startup, Sync API called with `sync_token="*"` for full snapshot; returned `sync_token` persisted to Postgres `kv_store`; on restart, stored token loaded for incremental sync | VERIFIED | `app/todoist/sync_manager.py`: `load_sync_token` returns `"*"` when no kv row; `save_sync_token` upserts+commits to `kv_store`; `startup_sync` loads token, fetches delta, saves new token BEFORE item loop (line 74 < line 81). Tests: `test_startup_sync_full_on_missing_token`, `test_startup_sync_incremental_on_stored_token`, `test_startup_sync_persists_token_before_processing`. |
| SC-3 | `extract_zoho_id(description)` correctly parses `[zoho:(\d+)]` from any position; returns `None` for missing footer; unit tests cover missing/mid-text/post-edit cases | VERIFIED | `app/todoist/normalise.py` lines 10–22: uses `ZOHO_ID_RE.search()` (imported from `app.core.normalise`, no re-definition). 9 unit tests: none/empty/missing/footer-at-end/mid-text/post-edit/non-digit/empty-id/return-type. All 9 pass. |
| SC-4 | Items without `[zoho:ID]` footer are logged (`todoist_item_no_footer_discarded`) and discarded in the Sync API delta path | VERIFIED | `app/todoist/sync_manager.py` lines 86–93: `extract_zoho_id(item.get("description"))` returns `None` → `discarded_no_footer += 1`, `log.info("todoist_item_no_footer_discarded", ...)`, `continue`. Test `test_startup_sync_discards_items_without_footer` confirms. |

### Plan-Level Must-Have Truths (from PLAN frontmatter)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| P1-T1 | `extract_zoho_id` returns the Zoho task ID string for any description containing `[zoho:NNN]` | VERIFIED | `ZOHO_ID_RE.search(description)` returns group(1) as str. Covered by `test_extract_zoho_id_footer_at_end`, `test_extract_zoho_id_mid_text`, `test_extract_zoho_id_after_user_edit`, `test_extract_zoho_id_returns_str`. |
| P1-T2 | `extract_zoho_id` returns None for None, empty string, missing footer, or non-digit IDs | VERIFIED | Lines 19–22 handle falsy descriptions; regex requires `\d+` so `[zoho:abc]` and `[zoho:]` return None. 4 tests confirm. |
| P1-T3 | `todoist_task_to_normalised` produces NormalisedTask with title/due_date/priority/is_completed — NEVER labels | VERIFIED | `app/todoist/normalise.py` lines 25–42: adapter never accesses `task.labels`; NormalisedTask has no labels field. `test_normalise_excludes_labels` and `test_normalise_ignores_labels_field` confirm structurally and behaviourally. |
| P2-T1 | `fetch_todoist_task` returns typed Task on 200 and raises typed exceptions on 401/404/429/5xx | VERIFIED | (Same as SC-1 above) |
| P2-T2 | `fetch_sync_delta` POSTs to `https://api.todoist.com/api/v1/sync` with sync_token + resource_types, returns (items, new_sync_token), filters client-side by project_id | VERIFIED | `client.py` lines 69–108: correct URL, form data `resource_types='["items"]'`, returns `(items, new_token)`, filters `[i for i in items if i.get("project_id") == project_id]`. 7 Sync API tests confirm including `test_sync_delta_filters_by_project_id`. |
| P2-T3 | 401 stops sync via typed exception rather than silent retry | VERIFIED | `TodoistAuthError` raised and not caught inside `TodoistClient`; `test_fetch_task_401_raises_auth_error` and `test_sync_delta_401_raises_auth_error` confirm. |
| P2-T4 | API token never logged in plaintext | VERIFIED | `grep "log.*api_token"` in `client.py` returns empty. Log calls reference `todoist_id` and `item_count`/`project_filtered` only. |
| P3-T1 | On first startup (no kv row), `load_sync_token` returns `"*"` | VERIFIED | `sync_manager.py` line 33: `if row is None or not row.value: return FULL_SYNC_SENTINEL`. Test: `test_load_sync_token_missing_returns_wildcard`. |
| P3-T2 | After `fetch_sync_delta` succeeds, returned `sync_token` persisted BEFORE item processing | VERIFIED | Line 74 (`await save_sync_token`) precedes line 81 (`for item in items:`). `test_startup_sync_persists_token_before_processing` enforces ordering via call_order spy. |
| P3-T3 | On restart with stored token, `load_sync_token` returns it for incremental sync | VERIFIED | Line 35: `return row.value`. Test: `test_load_sync_token_present_returns_value`. |
| P3-T4 | Footerless items logged and discarded in `startup_sync` | VERIFIED | (Same as SC-4 above) |
| P3-T5 | FastAPI lifespan: constructs TodoistClient, runs startup_sync, stores on app.state; shutdown calls close() | VERIFIED | `app/main.py` lines 12–13 imports, line 100 constructs, line 102 awaits startup_sync (before yield at line 108), line 106 stores on app.state, line 117 closes after yield. Tests: `test_lifespan_initialises_todoist_client_and_runs_startup_sync` and `test_lifespan_startup_sync_failure_propagates`. |

**Score: 9/9 roadmap success criteria + plan must-haves verified**

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/todoist/__init__.py` | Package marker | VERIFIED | File exists, empty (mirrors zoho/__init__.py) |
| `app/todoist/normalise.py` | `extract_zoho_id()` + `todoist_task_to_normalised()` | VERIFIED | 42 lines, imports ZOHO_ID_RE from core, no re.compile, no task.labels |
| `tests/unit/test_todoist_normalise.py` | Unit tests for SYNC-5, SYNC-8, SYNC-9 | VERIFIED | 15 test functions (9 extract + 6 adapter), all pass |
| `app/todoist/client.py` | `TodoistClient` + typed exceptions + `fetch_todoist_task` + `fetch_sync_delta` + `close` | VERIFIED | 108 lines, all 4 exception classes defined, async SDK used, SYNC_API_URL constant |
| `tests/unit/test_todoist_client.py` | Unit tests for all REST and Sync API paths | VERIFIED | 12 test functions, all pass |
| `app/todoist/sync_manager.py` | `KV_SYNC_TOKEN_KEY`, `load_sync_token`, `save_sync_token`, `startup_sync` | VERIFIED | All 4 exports present, upsert_kv imported (not redefined), no-footer discard implemented |
| `app/main.py` | Lifespan wired: TodoistClient + startup_sync + close | VERIFIED | Imports at lines 12–13, construction at line 100, startup_sync at line 102, stored at line 106, closed at line 117 |
| `tests/unit/test_todoist_sync_manager.py` | SEED-7 + SYNC-8 discard coverage | VERIFIED | 9 test functions, all pass |
| `tests/unit/test_main_lifespan.py` | Todoist lifespan wiring tests | VERIFIED | 2 new tests added: construction+close and failure propagation |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `app/todoist/normalise.py` | `app/core/normalise.py` | `from app.core.normalise import ZOHO_ID_RE, NormalisedTask, normalise_due_date, normalise_title` | WIRED | Line 7 imports all 4 symbols; no redefinition |
| `app/todoist/client.py` | `todoist_api_python.api_async.TodoistAPIAsync` | `self._api.get_task()` wrapped with typed exception mapping | WIRED | Line 14 imports, line 45 instantiates, line 53 calls get_task() |
| `app/todoist/client.py` | `https://api.todoist.com/api/v1/sync` | `httpx.AsyncClient` POST with Authorization Bearer + form data | WIRED | `SYNC_API_URL` constant line 21, POST at line 84 with Bearer header |
| `app/todoist/sync_manager.py` | `app.zoho.token_manager.upsert_kv` | Direct import — not duplicated | WIRED | Line 18: `from app.zoho.token_manager import upsert_kv`, used at line 44 |
| `app/todoist/sync_manager.py` | `app.todoist.client.TodoistClient.fetch_sync_delta` | Called with loaded sync_token and project_id | WIRED | Line 66–69: `todoist_client.fetch_sync_delta(sync_token=stored_token, project_id=settings.todoist_project_id)` |
| `app/todoist/sync_manager.py` | `app.todoist.normalise.extract_zoho_id` | Called on each item's description; None → log and skip | WIRED | Line 17 imports, line 86 calls `extract_zoho_id(item.get("description"))` |
| `app/main.py` | `app.todoist.sync_manager.startup_sync` | Awaited inside FastAPI lifespan before yield | WIRED | Line 13 imports, line 102 awaits; yield at line 108 (startup_sync line 102 < yield line 108) |

---

## Data-Flow Trace (Level 4)

Not applicable — no components render dynamic data in this phase. All artifacts are pure functions, HTTP client layers, or backend async processes writing to logs/DB. No UI rendering.

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 36 Phase 3 tests pass | `pytest tests/unit/test_todoist_normalise.py tests/unit/test_todoist_client.py tests/unit/test_todoist_sync_manager.py -q` | 36 passed | PASS |
| Full test suite green (no regressions) | `pytest tests/ -q` | 162 passed | PASS |
| `extract_zoho_id` module importable | Confirmed via pytest run | Import succeeds | PASS |
| `TodoistClient` uses async SDK | `grep "TodoistAPIAsync" app/todoist/client.py` | Found at line 14 and 45 | PASS |
| `startup_sync` persists token before item loop | Line ordering check: line 74 vs line 81 | save_sync_token(74) < for item in items(81) | PASS |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SYNC-5 | 03-01, 03-02 | `[zoho:(\d+)]` footer extraction; regex matches digits only; auth failures stop sync | SATISFIED | `extract_zoho_id` uses `ZOHO_ID_RE.search` (digits-only regex); `TodoistAuthError` raised and not caught internally; lifespan propagates failures |
| SYNC-8 | 03-01, 03-02, 03-03 | New Todoist-native tasks (without footer) are logged and discarded | SATISFIED | `startup_sync` discards footerless items via `extract_zoho_id() is None`; `test_startup_sync_discards_items_without_footer` verifies. `TodoistClient.fetch_sync_delta` returns items for caller to filter. |
| SYNC-9 | 03-01 | Labels never propagated to Zoho; excluded from sync path structurally | SATISFIED | `todoist_task_to_normalised` never accesses `task.labels`; `NormalisedTask` has no labels field. Both structural (`test_normalise_excludes_labels`) and behavioural (`test_normalise_ignores_labels_field`) tests pass. |
| SEED-7 | 03-03 | `sync_token` persisted to Postgres `kv_store`; resumes incrementally on restart; falls back to `"*"` on missing/empty | SATISFIED | `KV_SYNC_TOKEN_KEY = "todoist_sync_token"` in kv_store; `load_sync_token` returns `"*"` for missing/empty; `save_sync_token` commits immediately; token saved BEFORE item loop (crash-safe) |

---

## Anti-Patterns Found

| File | Pattern | Severity | Assessment |
|------|---------|----------|------------|
| None found | — | — | No TODO/FIXME/placeholder comments. No empty return stubs. No hardcoded empty data in production paths. `startup_sync` counts processed items without handoff — intentional (Phase 3 is read-only; Phase 5 wires pipeline). |

---

## Human Verification Required

None. All observable truths are verified programmatically via unit tests and code inspection.

---

## Gaps Summary

No gaps. All 9 roadmap success criteria and plan must-haves are verified. All 4 requirement IDs (SYNC-5, SYNC-8, SYNC-9, SEED-7) are satisfied. Full test suite passes at 162 tests with no regressions.

---

_Verified: 2026-04-24T00:00:00Z_
_Verifier: Claude (gsd-verifier)_
