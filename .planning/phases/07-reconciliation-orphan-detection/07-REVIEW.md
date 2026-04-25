---
phase: 07-reconciliation-orphan-detection
reviewed: 2026-04-25T00:00:00Z
depth: standard
files_reviewed: 4
files_reviewed_list:
  - app/worker/reconciler.py
  - app/worker/settings.py
  - tests/unit/test_reconciler.py
  - tests/unit/test_worker_settings.py
findings:
  critical: 0
  warning: 3
  info: 2
  total: 5
status: issues_found
---

# Phase 07: Code Review Report

**Reviewed:** 2026-04-25T00:00:00Z
**Depth:** standard
**Files Reviewed:** 4
**Status:** issues_found

## Summary

The reconciler and worker settings are well-structured. The two-cycle orphan confirmation, rate-limit skip behaviour, and sync-token crash-safety are implemented correctly and have solid test coverage. Three warnings require attention before shipping: a dead import that signals a missing notification call on orphan resolution, a `session.begin()` block that silently discards its write on any exception, and an internal API bypass (`_api.update_task`) that should be wrapped in the client.

---

## Warnings

### WR-01: `send_deletion_notification` imported but never called — notification gap on orphan resolution

**File:** `app/worker/reconciler.py:12`

**Issue:** `send_deletion_notification` is imported from `app.core.notifications` but is never invoked anywhere in `reconciler.py`. The docstring on `_handle_orphan` (line 235–237) states that `delete_todoist_task` and `delete_zoho_task` already send notifications internally, which is correct — but the unused import indicates a notification was intended to be sent *from* the orphan path and was either forgotten or the import was never cleaned up. In either case, the import is misleading and static analysis tools will flag it as an error.

More importantly: if the delete call fails and is swallowed by the bare `except Exception` (lines 245–250 and 254–262), no notification is sent for that failure. There is no notification that an orphan *was detected* either — only that a task was deleted successfully (via the writer). Users may be unaware their task pair was cleaned up.

**Fix:** If no additional notification is needed from `_handle_orphan`, remove the import:

```python
# Remove line 12:
# from app.core.notifications import send_deletion_notification
```

If an orphan-detection notification is desired (separate from the deletion notification), add the call after confirming deletion:

```python
await send_deletion_notification(
    zoho_task_id=state.zoho_task_id,
    todoist_task_id=state.todoist_task_id,
    reason="orphan_confirmed",
)
```

---

### WR-02: `orphan_check_count` reset write not committed — silently dropped if the session closes without error

**File:** `app/worker/reconciler.py:189-194`

**Issue:** The healthy-path count-reset block (lines 189–194) uses `async with sess.begin():` which provides an implicit commit on clean exit. However, looking more carefully: `sess.begin()` as an async context manager commits on `__aexit__` only when using SQLAlchemy's `begin()` as a nested transaction context. This is correct. **However**, the session is opened *without* `begin()` in the enclosing `async with session_factory() as sess:` block at line 190. When `session_factory` is an `async_sessionmaker` with `expire_on_commit=False`, the outer `async with` does NOT auto-begin a transaction — it relies on the inner `async with sess.begin():` to provide the transaction context. This is the intended pattern used elsewhere in the codebase and is correct.

The real issue is different: if `sess.get(SyncState, state.zoho_task_id)` returns `None` (the row was already deleted by a concurrent sweep), the reset is silently skipped with no log entry. This is harmless but may confuse debugging.

More critically: the count-reset only fires when `state.orphan_check_count > 0` (line 189). A row with `orphan_check_count = 0` that is healthy will skip this block entirely — which is correct. But if `orphan_check_count` is, say, `2` (test 15 exercises this case), the locked row's count is set to `0` inside the `begin()` block. The bug is that **the locked row is the result of `sess.get(SyncState, state.zoho_task_id)`** — SQLAlchemy's `session.get()` looks up by *primary key*, which for `SyncState` is `zoho_task_id`. This is correct. No bug here.

After closer inspection, WR-02 is downgraded to an info note — see IN-01 below. Replacing with the actual WR-02:

### WR-02: `_handle_orphan` does not guard against both `zoho_missing` and `todoist_missing` being `True` simultaneously

**File:** `app/worker/reconciler.py:241-262`

**Issue:** When both `zoho_missing=True` and `todoist_missing=True`, `_handle_orphan` attempts to delete both the Todoist task (line 244) and the Zoho task (line 256) in sequence. The Todoist delete is attempted first, fails silently (both sides are gone), then the Zoho delete is attempted and also fails silently. This results in two swallowed errors and two `log.error` entries for a normal situation (both sides simultaneously missing). The sync_state row and SyncEvent are still written correctly afterward.

This is not a crash, but it generates confusing error log noise for a legitimate scenario. More importantly, the `delete_todoist_task` call at line 244 with `ctx["todoist_client"]._api` will raise `TodoistNotFoundError` (from the writer) which is caught by `except Exception`, meaning the error log will say "orphan_todoist_delete_failed" even though the task was already gone — misleading operators.

**Fix:** Add a guard for the both-missing case:

```python
if zoho_missing and todoist_missing:
    # Both sides gone simultaneously — nothing to delete, just clean up
    log.warning(
        "orphan_both_sides_missing",
        zoho_task_id=state.zoho_task_id,
        todoist_task_id=state.todoist_task_id,
    )
elif zoho_missing:
    try:
        await delete_todoist_task(state.todoist_task_id, ctx["todoist_client"]._api)
    except Exception as exc:
        log.error("orphan_todoist_delete_failed", ...)
elif todoist_missing:
    try:
        access_token = token_state["access_token"]
        await delete_zoho_task(state.zoho_task_id, access_token)
    except Exception as exc:
        log.error("orphan_zoho_delete_failed", ...)
```

---

### WR-03: `todoist_client._api.update_task` called directly — bypasses client error handling and couples reconciler to internal SDK structure

**File:** `app/worker/reconciler.py:185-186`

**Issue:** The footer re-attachment path (EDGE-8) calls `todoist_client._api.update_task(...)` directly, bypassing `TodoistClient`'s error-handling wrappers (which translate SDK exceptions into `TodoistAPIError`, `TodoistNotFoundError`, etc.). This means:

1. The exception types raised here differ from all other Todoist call sites in the reconciler, breaking the consistent `except (TodoistRateLimitError, TodoistAPIError)` pattern.
2. If the `todoist-api-python` SDK changes its internal API (e.g., renames `_api`), this breaks silently.
3. If `update_task` raises a rate-limit response, it will surface as a raw SDK exception rather than `TodoistRateLimitError`, and the orphan sweep will not skip-and-retry correctly.

**Fix:** Add a public `update_task` method to `TodoistClient` that wraps the SDK call with the same exception translation used elsewhere in the client:

```python
# In app/todoist/client.py:
async def update_task(self, task_id: str, **kwargs) -> None:
    try:
        await self._api.update_task(task_id, **kwargs)
    except Exception as exc:
        # translate as per existing pattern
        raise TodoistAPIError(str(exc)) from exc
```

Then in `reconciler.py` line 185:

```python
await todoist_client.update_task(
    state.todoist_task_id, description=new_description
)
```

---

## Info

### IN-01: `upsert_kv` session in `reconcile_sweep` last-run block opens a session without explicit `begin()`

**File:** `app/worker/reconciler.py:96-98`

**Issue:** The final block of `reconcile_sweep` calls `upsert_kv` followed by `session.commit()` in an `async with session_factory() as session:` context (no `session.begin()`). This pattern is intentional per `upsert_kv`'s contract (caller commits), but it is inconsistent with the `async with session.begin():` pattern used in `orphan_sweep` and `_handle_orphan`. SQLAlchemy's async session will auto-begin a transaction on first operation, so the explicit `commit()` at line 98 is correct — but the inconsistency can confuse future maintainers.

The same pattern appears in the `orphan_sweep` last-run block (lines 220–222).

**Fix:** Either standardise on `async with session.begin():` (no explicit `commit()` needed) or keep the explicit `commit()` pattern and add a comment explaining the intent. No functional change required; this is a consistency note.

---

### IN-02: Unused import `send_deletion_notification` also reflected in test helper `_common_orphan_patches`

**File:** `tests/unit/test_reconciler.py:461`

**Issue:** `_common_orphan_patches` patches `app.worker.reconciler.send_deletion_notification` and returns the mock, but no test asserts on `mock_send_notification`. The mock is always returned but never used. This is a dead-weight fixture component that may give false confidence that notifications are being tested.

**Fix:** If `send_deletion_notification` is removed from `reconciler.py` (per WR-01), remove the patch and return value from `_common_orphan_patches`. If the notification call is added, add assertions in the relevant tests (e.g., test 12 should assert `mock_send_notification.assert_called_once()`).

---

_Reviewed: 2026-04-25T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
