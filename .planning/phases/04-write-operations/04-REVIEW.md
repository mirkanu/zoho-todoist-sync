---
phase: 04-write-operations
reviewed: 2026-04-24T00:00:00Z
depth: standard
files_reviewed: 5
files_reviewed_list:
  - app/main.py
  - app/todoist/writer.py
  - app/zoho/writer.py
  - tests/unit/test_todoist_writer.py
  - tests/unit/test_zoho_writer.py
findings:
  critical: 0
  warning: 3
  info: 3
  total: 6
status: issues_found
---

# Phase 4: Code Review Report

**Reviewed:** 2026-04-24T00:00:00Z
**Depth:** standard
**Files Reviewed:** 5
**Status:** issues_found

## Summary

Phase 4 introduces Todoist and Zoho write operations (create, update, complete, delete, write-back) wired into the FastAPI lifespan. The implementation is well-structured with correct priority mapping, proper `due_string="no date"` clearing in the update path (Pitfall 1), 207 partial-failure detection, and fire-and-forget Resend notifications. The test suite is thorough — all critical paths are covered.

Three warnings are flagged:

1. `_raise_typed` and `_zoho_handle` both lack `-> NoReturn` (or partial `NoReturn`) annotations, leaving type checkers blind to the fact that these functions always raise. This causes a real unbound-variable risk in `create_todoist_task`: if `add_task` raises an exception that is NOT an `httpx.HTTPStatusError` (e.g., a network timeout, a `TypeError` from a bad response), the `except` block is skipped, `_raise_typed` is never called, `task` remains unbound, and line 58 (`task.id`) raises an `UnboundLocalError` — masking the real error.
2. `_send_deletion_notification` is duplicated verbatim across `app/todoist/writer.py` and `app/zoho/writer.py`, including the hardcoded "from" address and recipient.
3. `complete_zoho_task` does not handle the case where `zoho_terminal_statuses_list` is an empty list — an `IndexError` would surface at runtime if the env var is misconfigured as empty.

---

## Warnings

### WR-01: Unbound variable `task` when `add_task` raises a non-httpx exception

**File:** `app/todoist/writer.py:56-58`

**Issue:** The `try/except httpx.HTTPStatusError` block only catches `httpx.HTTPStatusError`. If `todoist_api.add_task(...)` raises anything else — a network timeout (`httpx.TimeoutException`), a JSON parse error (`TypeError`), a `ValidationError` from the SDK — the exception propagates past the `except` block uncaught, bypassing `_raise_typed`. In that situation Python continues after the `except` and hits `log.info(..., todoist_id=task.id)` at line 58. Because `task` was only assigned inside the `try`, it is unbound at that point, causing a secondary `UnboundLocalError` that masks the original exception and corrupts the stack trace.

Additionally, `_raise_typed` is declared `-> None` but always raises; type checkers therefore do not treat the code after `_raise_typed(...)` as unreachable, making this class of bug invisible to static analysis.

**Fix:**
```python
def _raise_typed(status: int, context: str, cause: Exception) -> NoReturn:  # <- NoReturn
    ...

async def create_todoist_task(...) -> str:
    ...
    try:
        task = await todoist_api.add_task(...)
    except httpx.HTTPStatusError as exc:
        _raise_typed(exc.response.status_code, f"add_task zoho:{zoho_task_id}", exc)
    except Exception:
        raise  # let unexpected errors propagate cleanly without unbound `task`
    log.info("todoist_task_created", zoho_id=zoho_task_id, todoist_id=task.id)
    return task.id
```

Changing the return type annotation to `-> NoReturn` also lets mypy prove `task` is always bound after the `try` block, eliminating the static analysis gap.

---

### WR-02: `complete_zoho_task` crashes with `IndexError` if terminal statuses list is empty

**File:** `app/zoho/writer.py:81`

**Issue:** `get_settings().zoho_terminal_statuses_list[0]` raises an `IndexError` if `ZOHO_TERMINAL_STATUSES` is set to an empty string or whitespace. The startup check in `main.py` iterates over the list to warn about unknown values, but does not guard against an empty list itself. A misconfigured env var would silently pass startup and crash only when `complete_zoho_task` is first invoked.

**Fix:**
```python
async def complete_zoho_task(zoho_task_id: str, access_token: str) -> None:
    from app.core.config import get_settings
    statuses = get_settings().zoho_terminal_statuses_list
    if not statuses:
        raise ZohoAPIError("ZOHO_TERMINAL_STATUSES is empty — cannot complete task")
    terminal = statuses[0]
    ...
```

Alternatively (or additionally), add a validator in `Settings` that rejects an empty list at startup.

---

### WR-03: Duplicated `_send_deletion_notification` function with hardcoded recipient

**File:** `app/todoist/writer.py:113-125` and `app/zoho/writer.py:130-142`

**Issue:** The `_send_deletion_notification` helper is copy-pasted verbatim into both writer modules, including the hardcoded `"from"` address and `"to"` recipient list. Any future change (verified sender domain, configurable recipient) must be applied in two places. A divergence will cause one module to use the wrong sender or recipient without any error.

**Fix:** Extract to a shared module (e.g., `app/core/notifications.py`):
```python
# app/core/notifications.py
async def send_deletion_notification(subject: str, html: str) -> None:
    """Fire-and-forget Resend email. Failure is logged, never re-raised."""
    from app.core.config import get_settings
    settings = get_settings()
    try:
        params: resend.Emails.SendParams = {
            "from": settings.resend_from_address,   # or a constant
            "to": [settings.notification_email],    # or a constant
            "subject": subject,
            "html": html,
        }
        await resend.Emails.send_async(params)
    except Exception as exc:
        log.error("resend_email_failed", error=str(exc))
```

---

## Info

### IN-01: `_raise_typed` / `_zoho_handle` return type annotations should be `-> NoReturn`

**File:** `app/todoist/writer.py:29` and `app/zoho/writer.py:31`

**Issue:** Both helpers are annotated `-> None` / `-> Any` but always either raise or return (for `_zoho_handle`). `_raise_typed` always raises; its annotation should be `-> NoReturn` to enable mypy to prove unreachability after calls to it. `_zoho_handle` returns on success paths and raises on error paths — it cannot be `NoReturn`, but the `-> Any` annotation on a function that raises typed exceptions is correct; no change needed there. The actionable change is `_raise_typed: -> NoReturn`.

**Fix:**
```python
from typing import NoReturn

def _raise_typed(status: int, context: str, cause: Exception) -> NoReturn:
    ...
```

---

### IN-02: `ZOHO_ID_RE` only matches digit-only Zoho IDs; Zoho IDs are numeric in practice but the regex is undocumented

**File:** `app/core/normalise.py:8`

**Issue:** `ZOHO_ID_RE = re.compile(r"\[zoho:(\d+)\]")` restricts matched IDs to digit sequences. Zoho CRM record IDs are currently 18-digit integers, so this works today. However the constraint is not documented and the footer writer in `todoist/writer.py:50` does not validate that `zoho_task_id` is numeric before embedding it. A non-numeric ID (e.g., from a future API version or a misconfiguration) would be written to the footer but never parsed back by `ZOHO_ID_RE`, silently breaking the linkage.

**Fix:** Add a comment documenting the numeric constraint, or widen the regex:
```python
# Zoho CRM record IDs are 18-digit integers. If Zoho ever changes this,
# update this pattern and the writer's footer string together.
ZOHO_ID_RE = re.compile(r"\[zoho:([^\]]+)\]")
```

---

### IN-03: Hardcoded email address in both writer modules

**File:** `app/todoist/writer.py:118` and `app/zoho/writer.py:135`

**Issue:** `"to": ["manuelkuhs@gmail.com"]` is hardcoded in both `_send_deletion_notification` functions. While acceptable for a personal sync service, it would be cleaner to derive this from the environment (e.g., a `NOTIFICATION_EMAIL` env var) to avoid a code change when the recipient needs to change.

**Fix:** Add `notification_email: str = "manuelkuhs@gmail.com"` to `Settings` (with a sensible default) and reference it from the notification function. This is low-priority for a personal service but would be required before sharing the codebase.

---

_Reviewed: 2026-04-24T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
