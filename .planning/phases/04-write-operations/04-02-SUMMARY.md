---
phase: 04-write-operations
plan: "02"
subsystem: zoho-writer
tags: [zoho, write, httpx, resend, tdd]
completed: "2026-04-24"
duration_minutes: 11

dependency_graph:
  requires:
    - app/zoho/client.py         # typed exceptions + ZOHO_EU_BASE_URL
    - app/zoho/state.py          # zoho_field_cache
    - app/core/priority.py       # todoist_to_zoho_priority
    - app/core/config.py         # get_settings / zoho_terminal_statuses_list
    - app/core/normalise.py      # NormalisedTask
  provides:
    - app/zoho/writer.py         # update_zoho_task, complete_zoho_task, delete_zoho_task, write_todoist_id_to_zoho
  affects:
    - Phase 5 sync_task worker   # consumes all four writer functions

tech_stack:
  added: []
  patterns:
    - httpx.AsyncClient per-call (matches app/zoho/client.py convention)
    - _zoho_handle module-level function with 207 per-record dispatch
    - lazy import of get_settings inside complete_zoho_task (matches token_manager.py pattern)
    - resend fire-and-forget with exception swallowing (EDGE-6)

key_files:
  created:
    - app/zoho/writer.py
    - tests/unit/test_zoho_writer.py
  modified: []

decisions:
  - "Lazy import of get_settings inside complete_zoho_task body — top-level import of app.core.config triggers module-level settings=get_settings() which fails at pytest collection time when env vars not yet set. Matches token_manager.py pattern."

metrics:
  duration_minutes: 11
  completed: "2026-04-24"
  tasks_completed: 2
  files_created: 2
  tests_added: 18
---

# Phase 4 Plan 2: Zoho Writer Summary

One-liner: Zoho CRM v8 write module implementing update/complete/delete/write-todoist-id via raw httpx, with Resend deletion notifications and full TDD coverage (18 tests GREEN).

## Artifacts Created

### `app/zoho/writer.py` (142 lines)

Four async functions + two helpers:

- `update_zoho_task(zoho_task_id, normalised, access_token)` — PUT `{Subject, Due_Date, Priority}` to `/Tasks/{id}`; `Due_Date=None` serialises to JSON `null` (EDGE-3/SYNC-3)
- `complete_zoho_task(zoho_task_id, access_token)` — PUT `{Status: terminal}` using `zoho_terminal_statuses_list[0]`; hardcoded `"Completed"` absent (EDGE-4)
- `delete_zoho_task(zoho_task_id, access_token)` — DELETE `/Tasks/{id}`; 404 returns early without email (Pitfall 5); success sends Resend notification (EDGE-2); Resend failure logged, not re-raised (EDGE-6)
- `write_todoist_id_to_zoho(zoho_task_id, todoist_task_id, access_token)` — PUT `{field_api_name: todoist_task_id}` using `zoho_field_cache["todoist_task_id_api_name"]`; raises `ZohoAPIError` if field unresolved (SYNC-6/Pitfall 3)
- `_zoho_handle(resp, context)` — maps 401/404/429/other to typed exceptions; 207 inspected per-record (Pitfall 2)
- `_send_deletion_notification(subject, html)` — fire-and-forget Resend email to `manuelkuhs@gmail.com`

### `tests/unit/test_zoho_writer.py` (272 lines)

18 tests collected (15 test functions, priority roundtrip parametrized to 4 cases):

| Test | Coverage |
|------|----------|
| `test_update_zoho_task_correct_payload` | SYNC-3: exact PUT body shape + auth header |
| `test_update_zoho_task_clears_due_date_with_null` | EDGE-3: `Due_Date` key present with `null` value |
| `test_update_zoho_task_priority_roundtrip` [x4] | Priority mapping 4→Highest, 3→High, 2→Normal, 1→Low |
| `test_update_zoho_task_401/404/429_raises_*` | Typed exception dispatch |
| `test_update_zoho_task_207_with_error_raises` | Pitfall 2: 207 error status → ZohoAPIError |
| `test_update_zoho_task_207_success_passes` | Pitfall 2: 207 success → no exception |
| `test_complete_zoho_task_uses_terminal_status` | EDGE-4: configurable terminal status ("Done") |
| `test_complete_zoho_task_default_completed` | EDGE-4: default "Completed" from env |
| `test_delete_zoho_task_sends_email` | EDGE-2: Resend email sent on successful delete |
| `test_delete_zoho_task_404_idempotent_no_email` | Pitfall 5: 404 → no raise, no email |
| `test_delete_zoho_task_resend_failure_does_not_raise` | EDGE-6: Resend exception swallowed |
| `test_write_todoist_id_to_zoho_uses_cached_field_name` | SYNC-6: dynamic field name from cache |
| `test_write_todoist_id_to_zoho_raises_when_field_not_resolved` | Pitfall 3: guard clause before HTTP call |

**Result: 18/18 GREEN. Full suite: 180/180 GREEN (no regression).**

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Lazy import of `get_settings` inside `complete_zoho_task`**

- **Found during:** Task 2 (GREEN phase) — tests failed with `ValidationError` at collection time
- **Issue:** Plan specified `from app.core.config import get_settings` as a top-level import in `writer.py`. However, importing `app.core.config` at module level triggers `settings = get_settings()` (line 38 of config.py) before pytest fixtures set env vars. This causes `pydantic_settings.ValidationError` at collection time.
- **Fix:** Removed top-level `from app.core.config import get_settings`; added lazy import inside `complete_zoho_task` body — matches the established pattern in `app/zoho/token_manager.py`.
- **Files modified:** `app/zoho/writer.py`
- **Commit:** `deb5857`

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes introduced. All threat model items (T-04-07 through T-04-13) addressed:

- **T-04-07:** Log calls use only `zoho_id`, `todoist_id`, `status`, `error` fields — confirmed no `access_token=` in any log call
- **T-04-08:** Email body contains only `zoho_task_id` literal
- **T-04-09:** `normalised.priority` goes through `todoist_to_zoho_priority` mapping with fallback; `normalised.title` pre-normalised by Phase 1
- **T-04-10:** 404 short-circuit returns before `_send_deletion_notification` — enforced by `test_delete_zoho_task_404_idempotent_no_email`
- **T-04-11:** `log.error("resend_email_failed", error=str(exc))` emits on every swallowed exception
- **T-04-12:** Accepted — `zoho_task_id` originates from trusted Zoho sources
- **T-04-13:** Guard clause raises `ZohoAPIError` before HTTP call when `field_api_name` missing — enforced by `test_write_todoist_id_to_zoho_raises_when_field_not_resolved`

## Known Stubs

None — all four write functions are fully wired. `sync-alerts@resend.dev` sender address is a placeholder (A3: update to verified domain in Phase 8); this is intentional and documented inline.

## Self-Check: PASSED

- `app/zoho/writer.py` exists: FOUND
- `tests/unit/test_zoho_writer.py` exists: FOUND
- Task 1 commit `98f89ab` exists: FOUND
- Task 2 commit `deb5857` exists: FOUND
- `pytest tests/unit/test_zoho_writer.py`: 18 passed
- `pytest tests/`: 180 passed
