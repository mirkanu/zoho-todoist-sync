---
phase: 06-webhooks
plan: "02"
subsystem: web
tags: [fastapi, webhooks, todoist, hmac, security, tdd]
dependency_graph:
  requires:
    - 06-01 (app/webhooks/router.py stub, app.state.redis, app.state.session_factory)
    - 05-02 (enqueue_sync, ArqRedis)
    - 03-xx (extract_zoho_id from app.todoist.normalise)
    - 01-xx (SyncState model, idx_sync_state_todoist_task_id index)
  provides:
    - POST /webhooks/todoist: full HMAC-SHA256 verification + event dispatch
    - _lookup_zoho_id helper: indexed sync_state SELECT by todoist_task_id
    - SYNC-8: item:added without footer discarded (no enqueue)
    - LOOP-5: item:added with footer enqueues via extract_zoho_id
    - EDGE-7: item:completed propagates to Zoho via enqueue_sync
    - EDGE-8: missing sync_state row → WARN + 200, no enqueue
    - T-06-09/10/11: spoofing/timing/tampering mitigations via raw-body HMAC
  affects:
    - app/webhooks/router.py (Todoist stub replaced; Zoho handler unchanged)
    - tests/unit/test_webhooks.py (15 new Todoist tests added; old permissive stub replaced)
tech_stack:
  added: []
  patterns:
    - Raw-body HMAC-SHA256 (await request.body() before request.json())
    - hmac.compare_digest for constant-time comparison
    - Single indexed SELECT via SQLAlchemy select(SyncState.zoho_task_id).where(...)
    - Project_id gate before any DB read (DoS mitigation)
    - str() coercion on event_data.id for int tolerance
key_files:
  created: []
  modified:
    - app/webhooks/router.py (Todoist stub replaced with full 119-line handler)
    - tests/unit/test_webhooks.py (expanded from 10 to 25 tests)
decisions:
  - "raw_body captured via await request.body() before request.json() — Starlette caches the body so both calls work correctly"
  - "_lookup_zoho_id extracted as module-level async helper for testability and single-responsibility"
  - "project_id gate placed before redis/session_factory access to avoid DB reads for foreign-project events"
  - "item:added footer path uses extract_zoho_id directly (no DB lookup) — zoho_id is in the payload"
  - "EDGE-8 WARN log emitted for item:updated/completed/uncompleted with no sync_state row"
metrics:
  duration: "8 minutes"
  completed_date: "2026-04-24"
  tasks_completed: 2
  files_changed: 2
---

# Phase 6 Plan 02: Todoist Webhook Handler (HMAC + Event Dispatch) Summary

**One-liner:** Full Todoist webhook handler with raw-body HMAC-SHA256 via `hmac.compare_digest`, event_name dispatch across 5 event types, `sync_state` indexed lookup, and LOOP-5/SYNC-8/EDGE-7/EDGE-8 routing logic.

## What Was Built

### app/webhooks/router.py (Todoist handler replacement)

The Plan 01 stub (`return {"ok": True}`) was replaced with a complete 119-line handler. The Zoho handler is byte-identical to Plan 01.

**HMAC verification (T-06-09/10/11):**
- `raw_body = await request.body()` — captured before `request.json()` call
- `base64(HMAC-SHA256(raw_body, TODOIST_CLIENT_SECRET))` computed via `hmac.new(...).digest()`
- `hmac.compare_digest(expected, received)` — constant-time comparison; `==` forbidden
- Missing or mismatched header → `HTTPException(status_code=401)`

**_lookup_zoho_id(session_factory, todoist_task_id) helper:**
- Single `select(SyncState.zoho_task_id).where(SyncState.todoist_task_id == todoist_task_id)`
- Uses `idx_sync_state_todoist_task_id` index → O(log n)
- Returns `str | None` via `scalar_one_or_none()`

**Event dispatch:**
- `project_id` gate: events for other Todoist projects discarded before any DB read
- `item:added`: `extract_zoho_id(description)` — None → log + 200 (SYNC-8); present → `enqueue_sync(redis, zoho_id, defer_secs=0)` (LOOP-5)
- `item:updated` / `item:completed` / `item:uncompleted`: `_lookup_zoho_id` → enqueue or WARN+200 (EDGE-8)
- `item:deleted`: same lookup path; None → log+200; present → enqueue
- Unknown event_name: DEBUG log + 200
- `str(event_data.get("id", ""))` coercion tolerates int ids (T-06-14)

### tests/unit/test_webhooks.py (25 tests total, up from 10)

**New helpers:**
- `_make_hmac(body, secret)` — computes correct base64 HMAC for test requests
- `_mock_session_factory_returning(zoho_id)` — async context manager mock returning scalar result

**New tests (15):**
- `test_todoist_webhook_without_hmac_returns_401_post_plan02` — replaces Plan 01 permissive stub
- `test_todoist_invalid_hmac_returns_401` — bad header → 401, no enqueue, no DB
- `test_todoist_no_hmac_header_returns_401` — absent header → 401
- `test_todoist_hmac_uses_raw_body_not_parsed_json` — non-default whitespace body proves raw-bytes signing
- `test_todoist_hmac_compare_uses_compare_digest` — code-level grep; forbids `==` comparison
- `test_todoist_item_added_no_footer_discarded` — SYNC-8
- `test_todoist_item_added_with_footer_enqueues` — LOOP-5
- `test_todoist_item_updated_enqueues`
- `test_todoist_item_completed_enqueues` — EDGE-7
- `test_todoist_item_uncompleted_enqueues`
- `test_todoist_item_deleted_enqueues_when_sync_state_exists`
- `test_todoist_item_deleted_no_sync_state_logs_and_returns_200`
- `test_todoist_missing_footer_on_synced_task` — EDGE-8
- `test_todoist_wrong_project_id_discarded`
- `test_todoist_unknown_event_name_returns_200`
- `test_todoist_event_data_id_as_int_tolerated` — T-06-14

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — the Todoist stub from Plan 01 has been fully replaced.

## Threat Surface Scan

All STRIDE mitigations from the plan's threat register are implemented:

| Threat ID | Mitigation | Status |
|-----------|-----------|--------|
| T-06-09 | HMAC-SHA256 on raw body; 401 before any processing | Implemented |
| T-06-10 | `hmac.compare_digest` constant-time; anti-regression grep in tests | Implemented |
| T-06-11 | Raw bytes captured before `request.json()` | Implemented |
| T-06-12 | 401 path logs nothing about body; only raises HTTPException | Implemented |
| T-06-13 | project_id gate + item:added footer-check skip DB reads; arq _job_id dedup | Implemented |
| T-06-14 | `str(event_data.get("id", ""))` coercion | Implemented |
| T-06-15 | Request already HMAC-verified before footer is trusted; worker refetches Zoho | Implemented |
| T-06-16 | SQLAlchemy parameterises `todoist_task_id` value | Implemented |
| T-06-17 | Accepted per plan | Accepted |

No new trust boundaries introduced beyond what the plan's threat model covers.

## Self-Check

### Files exist:
- app/webhooks/router.py: FOUND (175 lines, >= 120 min)
- tests/unit/test_webhooks.py: FOUND (691 lines, 25 tests >= 22 min)

### Commits exist:
- 961a0cf (test RED phase): FOUND
- 3e37391 (feat GREEN phase): FOUND

## Self-Check: PASSED
