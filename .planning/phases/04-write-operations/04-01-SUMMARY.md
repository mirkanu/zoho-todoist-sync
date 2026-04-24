---
phase: 04-write-operations
plan: "01"
subsystem: todoist-writer
tags: [todoist, write-operations, resend, tdd]
dependency_graph:
  requires: [app/todoist/client.py, app/core/normalise.py, app/core/config.py, app/core/logging.py]
  provides: [app/todoist/writer.py]
  affects: [app/main.py]
tech_stack:
  added: []
  patterns: [standalone-async-functions, lazy-get_settings-import, fire-and-forget-resend, typed-exception-reraise]
key_files:
  created:
    - app/todoist/writer.py
    - tests/unit/test_todoist_writer.py
  modified:
    - app/main.py
decisions:
  - "Lazy import of get_settings inside create_todoist_task body to avoid module-level Settings() validation at import time (config.py has a module-level settings = get_settings() alias)"
  - "due_string='no date' for clearing Todoist due dates — SDK kwargs_without_none silently drops due_date=None (Pitfall 1)"
  - "Resend sender set to sync-alerts@resend.dev placeholder; to be replaced with verified domain in Phase 8 ops review (A3)"
metrics:
  duration: "~15 min"
  completed_date: "2026-04-24"
  tasks_completed: 2
  files_changed: 3
---

# Phase 4 Plan 01: Todoist Writer Summary

**One-liner:** Todoist write module (create/update/complete/delete) with Resend deletion email and due-date-clear via `due_string="no date"`, wired into FastAPI lifespan.

## Artifacts Created

| Artifact | Lines | Description |
|----------|-------|-------------|
| `app/todoist/writer.py` | 125 | Four async write functions + `_raise_typed` + `_send_deletion_notification` |
| `tests/unit/test_todoist_writer.py` | 224 | 12 pytest-asyncio tests covering all functions, error taxonomy, idempotency |
| `app/main.py` (edit) | +2 | `import resend` + `resend.api_key = settings.resend_api_key` in lifespan |

## Test Results

- **12 / 12 writer tests GREEN**
- **174 / 174 full suite GREEN** (no regressions in Phase 1/2/3 tests)
- TDD gate: RED commit `8be5506`, GREEN commit `0156764`

## Requirements Covered

| Req ID | Description | Status |
|--------|-------------|--------|
| SYNC-1 | Zoho tasks appear in Todoist | create_todoist_task implemented |
| SYNC-2 | due_date as date object, never due_datetime | Enforced; test asserts `isinstance(due_date, date)` and `"due_datetime" not in kwargs` |
| SYNC-8 | Footer `\n\n---\n[zoho:ID]` appended on create | Hardcoded f-string in create_todoist_task |
| EDGE-1 | Reassignment → delete Todoist task + Resend email | delete_todoist_task + _send_deletion_notification |
| EDGE-3 | Null due_date uses `due_string="no date"` | update_todoist_task: `else: kwargs["due_string"] = "no date"` |
| EDGE-6 | Resend failure logged, not re-raised | try/except in _send_deletion_notification; log.error only |
| EDGE-7 | Complete via SDK complete_task | complete_todoist_task calls `todoist_api.complete_task(task_id)` |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Lazy import of `get_settings` to avoid import-time Settings validation**

- **Found during:** Task 2 (GREEN) — first test run failed with pydantic ValidationError on collection
- **Issue:** `app/core/config.py` line 38 has `settings = get_settings()` as a module-level alias. When `writer.py` imported `from app.core.config import get_settings` at the top level, this triggered `config.py` evaluation, which called `Settings()` requiring all env vars — crashing pytest collection before `complete_env` fixture ran.
- **Fix:** Moved `from app.core.config import get_settings` inside the `create_todoist_task` function body (lazy import). This is consistent with how `sync_manager.py` uses `TYPE_CHECKING` to avoid the same trap.
- **Files modified:** `app/todoist/writer.py`
- **Commit:** `0156764`

## Known Stubs

- `"from": "sync-alerts@resend.dev"` — placeholder sender address in `_send_deletion_notification`. Resend requires a verified sender domain. This email will fail to send until a verified domain is configured. Marked A3 in RESEARCH.md; deferred to Phase 8 ops review. The failure is caught and logged (EDGE-6), so the service continues correctly.

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes introduced. `app/todoist/writer.py` makes outbound HTTPS to Todoist API (token in env) and Resend API (key in env) — both already in the threat model (T-04-01 through T-04-06). Threat mitigations verified:

- T-04-01: Log calls use only IDs (`zoho_id`, `todoist_id`), never tokens or task body
- T-04-02: Email body contains only the Todoist task ID
- T-04-04: 404 on delete returns early before `_send_deletion_notification` (tested by `test_delete_todoist_task_idempotent_when_already_gone`)
- T-04-05: `log.error("resend_email_failed", error=str(exc))` on swallowed Resend exceptions

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| `app/todoist/writer.py` exists | FOUND |
| `tests/unit/test_todoist_writer.py` exists | FOUND |
| `04-01-SUMMARY.md` exists | FOUND |
| RED commit `8be5506` exists | FOUND |
| GREEN commit `0156764` exists | FOUND |
| 12/12 writer tests GREEN | PASSED |
| 174/174 full suite GREEN | PASSED |
