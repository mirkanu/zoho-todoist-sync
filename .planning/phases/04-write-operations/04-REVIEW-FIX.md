---
phase: 04-write-operations
fixed_at: 2026-04-24T09:11:42Z
review_path: .planning/phases/04-write-operations/04-REVIEW.md
iteration: 1
findings_in_scope: 3
fixed: 3
skipped: 0
status: all_fixed
---

# Phase 4: Code Review Fix Report

**Fixed at:** 2026-04-24T09:11:42Z
**Source review:** .planning/phases/04-write-operations/04-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 3
- Fixed: 3
- Skipped: 0

## Fixed Issues

### WR-01: Unbound variable `task` when `add_task` raises a non-httpx exception

**Files modified:** `app/todoist/writer.py`
**Commit:** 611ef6e
**Applied fix:** Added `from typing import NoReturn` import and changed `_raise_typed` return annotation from `-> None` to `-> NoReturn`. Added a bare `except Exception: raise` clause after the `except httpx.HTTPStatusError` block in `create_todoist_task`, ensuring unexpected exceptions propagate cleanly before reaching `task.id` on line 61 (preventing an `UnboundLocalError` masking the real error).

### WR-02: `complete_zoho_task` crashes with `IndexError` if terminal statuses list is empty

**Files modified:** `app/zoho/writer.py`
**Commit:** 023c8d1
**Applied fix:** Extracted `get_settings().zoho_terminal_statuses_list` into a local `statuses` variable and added a `if not statuses: raise ZohoAPIError(...)` guard before the `[0]` index access, so a misconfigured empty `ZOHO_TERMINAL_STATUSES` env var raises a clear error rather than an `IndexError`.

### WR-03: Duplicated `_send_deletion_notification` function with hardcoded recipient

**Files modified:** `app/core/notifications.py` (new), `app/todoist/writer.py`, `app/zoho/writer.py`
**Commit:** f04de07
**Applied fix:** Created `app/core/notifications.py` with a single `send_deletion_notification(subject, html)` function containing the shared Resend logic. Removed the `import resend` and local `_send_deletion_notification` from both writer modules, replacing them with `from app.core.notifications import send_deletion_notification` and updated call sites to use the shared function.

---

_Fixed: 2026-04-24T09:11:42Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
