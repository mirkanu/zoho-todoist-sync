---
phase: 05-arq-worker
reviewed: 2026-04-24T00:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - app/worker/enqueue.py
  - app/worker/__init__.py
  - app/worker/jobs.py
  - app/worker/__main__.py
  - app/worker/settings.py
  - tests/unit/test_worker_jobs.py
  - tests/unit/test_worker_settings.py
findings:
  critical: 0
  warning: 4
  info: 3
  total: 7
status: issues_found
---

# Phase 05: Code Review Report

**Reviewed:** 2026-04-24T00:00:00Z
**Depth:** standard
**Files Reviewed:** 7
**Status:** issues_found

## Summary

The arq worker implementation is well-structured. The sync pipeline follows the documented design (SETNX lock, SELECT FOR UPDATE, LWW conflict resolution, echo suppression). Test coverage is thorough across all key paths. No security vulnerabilities or data-loss bugs were found.

Four warnings are present: (1) the SETNX lock is silently dropped when not acquired, which permanently loses a sync event rather than retrying; (2) the `todoist_to_zoho` write path uses `token_state` module global directly, meaning a stale or empty dict causes a `KeyError` crash inside the SELECT FOR UPDATE transaction; (3) the `_execute_sync` function reads `sync_state` without a lock then re-fetches it with `SELECT FOR UPDATE` in a second session — the first read is thrown away, making it a wasted round-trip with a subtle TOCTOU window; and (4) `enqueue_sync` passes `defer_secs` (an `int`) directly as `_defer_by` but arq expects a `timedelta` or seconds-as-float — passing an `int` works in most versions but the type contract should be explicit.

---

## Warnings

### WR-01: Lock not acquired — silent discard silently loses the sync event

**File:** `app/worker/jobs.py:83-84`
**Issue:** When the Redis SETNX lock is not acquired (another worker is processing the same task), the job returns `None` immediately with only a warning log. The comment says "reconciler will catch any missed sync within 15 min", but the reconciler does not exist yet (Phase 7). In the interim, any webhook that fires while a lock is held will be permanently dropped. The problem compounds if the existing lock-holder raises an exception that keeps it busy for the full 30 s TTL — new invocations keep skipping until the TTL expires.

More critically: when `_defer_by` is a non-zero integer (Zoho webhook path), the deferred job already carries a job_id of `sync:{zoho_task_id}`. If the first job hasn't started yet (still deferred), a second webhook fires and also tries to enqueue with the same `_job_id` — arq deduplicates to `None`, `enqueue_sync` logs a warn, but the defer_secs of the *second* event may differ from the first (e.g. the first was `defer_secs=0` from reconciler, second is `defer_secs=2` from webhook). The deduplication silently wins with whichever arrived first — not necessarily the fresher one.

This is documented behaviour (SYNC-10) so no change is strictly required in Phase 5, but the comment on line 84 should reference the gap explicitly so it isn't overlooked.

**Fix:**
```python
# Line 83-84 — strengthen the comment to make the gap explicit
if not acquired:
    log.warning(
        "sync_task_lock_not_acquired",
        zoho_task_id=zoho_task_id,
        # TODO(Phase 7): reconciler will re-sync within 15 min.
        # Until Phase 7 ships, this sync event is permanently lost
        # if the lock-holder finishes before the next webhook fires.
    )
    return
```

---

### WR-02: `token_state["access_token"]` KeyError inside SELECT FOR UPDATE transaction

**File:** `app/worker/jobs.py:212`
**Issue:** `_apply_write` reads `token_state["access_token"]` at call time, inside the open `session.begin()` transaction. If `token_state` is empty (e.g. the startup refresh failed silently, or a test didn't populate it), this raises `KeyError`. The exception propagates out of the `async with session.begin()` block, which causes SQLAlchemy to roll back the transaction — but the arq job then reaches the generic exception handler (none is present in `_execute_sync`), bubbles up to `sync_task`, and is NOT caught by the `(ZohoAPIError, ..., TodoistAPIError)` guard. The result is an unhandled exception that arq marks as a job failure and retries — potentially retrying an operation that will always fail until the token is restored.

A secondary concern: `token_state` is a module-level mutable dict imported by reference, but `_apply_write` does not hold any lock on it. In CPython dict reads are atomic, so there is no race, but the absence of a fallback for a missing key is fragile.

**Fix:**
```python
# In _apply_write, replace direct dict access with a safe read
access_token = token_state.get("access_token")
if not access_token:
    raise RuntimeError(
        "Zoho access_token not available in token_state; "
        "startup may have failed or token expired before refresh."
    )
```

---

### WR-03: Double DB round-trip in `_execute_sync` — first `SELECT` result is discarded

**File:** `app/worker/jobs.py:122-147`
**Issue:** `_execute_sync` performs two separate `session_factory()` calls:
1. Lines 122–126: `SELECT sync_state WHERE zoho_task_id = ?` (no lock) — result used only to check `state is None`.
2. Lines 140–147: Re-fetches the same row with `SELECT ... WITH FOR UPDATE` inside a transaction.

The row fetched at step 1 is completely discarded if it is not `None`. The first read serves only as an existence check; the authoritative state comes from the second locked read. This is correct for correctness, but it creates a subtle TOCTOU window: between the two reads another worker could insert a `SyncState` row for the same `zoho_task_id` (unlikely but possible if two webhooks fire simultaneously before the dedup lock key expires). In that case `scalar_one()` at line 147 would still succeed, so there is no crash — but the duplicated new-task path (`_handle_new_task`) could run twice, creating two Todoist tasks.

The real race: both workers pass the `state is None` check (line 128) and both call `_handle_new_task`. The second `session.add(SyncState(...))` would violate the unique constraint on `zoho_task_id`, raising an `IntegrityError` — arq would retry, but the first worker already created the Todoist task and wrote the ID back to Zoho.

**Fix:** Replace the unlocked first read with an early `SELECT ... FOR UPDATE` existence check inside a transaction, or — simpler — rely solely on the SETNX lock (already acquired at line 81) as the dedup guard, and remove the first unlocked read entirely:

```python
# Remove lines 122-130 (unlocked read) and do a single locked fetch:
async with session_factory() as session:
    async with session.begin():
        locked = await session.execute(
            select(SyncState)
            .where(SyncState.zoho_task_id == zoho_task_id)
            .with_for_update()
        )
        state = locked.scalar_one_or_none()

        if state is None:
            # Handle new task inline (within this transaction)
            ...
        else:
            # Proceed with hash compare and write
            ...
```

This eliminates the double round-trip and the TOCTOU window.

---

### WR-04: `_defer_by` receives `int` instead of `timedelta` — may silently misbehave on arq version change

**File:** `app/worker/enqueue.py:35`
**Issue:** arq's `enqueue_job` `_defer_by` parameter expects a `datetime.timedelta`. Passing a bare `int` (seconds) currently works because arq internally converts numeric values, but this is an implementation detail. The type annotation in arq stubs is `timedelta`, and some versions raise a `TypeError` on non-timedelta values. The test at `test_worker_settings.py:287` asserts `_defer_by == 2` (an int), which passes today but would break if arq tightens the check.

**Fix:**
```python
# enqueue.py — convert defer_secs to timedelta explicitly
from datetime import timedelta

job = await redis.enqueue_job(
    "sync_task",
    zoho_task_id,
    _job_id=f"sync:{zoho_task_id}",
    _defer_by=timedelta(seconds=defer_secs),
)
```

Update the test assertion correspondingly:
```python
from datetime import timedelta
assert call_kwargs.kwargs["_defer_by"] == timedelta(seconds=2)
```

---

## Info

### IN-01: `_execute_sync` `job_try` parameter is accepted but never used

**File:** `app/worker/jobs.py:110-116`
**Issue:** `_execute_sync` accepts `job_try: int` as a parameter but never references it. The retry delay logic lives in `sync_task` (lines 90-99), so `job_try` is not needed inside `_execute_sync`. The unused parameter is dead code that inflates the function signature.

**Fix:** Remove `job_try` from the `_execute_sync` signature and the call at line 88.

---

### IN-02: `TodoistNotFoundError` handler does not release the lock before returning

**File:** `app/worker/jobs.py:104-105`
**Issue:** When `TodoistNotFoundError` is raised, execution falls through to the `finally` block which calls `redis.delete(lock_key)` — so the lock IS released. This is correct. However, the handler at line 104 does nothing other than log a warning, with a comment that orphan handling is Phase 7 territory. This is intentional but worth an explicit note that Phase 7 must handle the case where `state.todoist_task_id` points to a deleted Todoist task (the sync_state row references a non-existent task indefinitely until Phase 7 runs).

**Fix:** Add a TODO comment:
```python
except TodoistNotFoundError:
    # TODO(Phase 7): todoist_task_id in sync_state is now orphaned.
    # Phase 7 reconciler must detect and re-create or de-link this row.
    log.warning("sync_task_todoist_not_found", zoho_task_id=zoho_task_id)
```

---

### IN-03: Test 1 asserts `redis.delete` not called, but the implementation calls it on lock failure

**File:** `tests/unit/test_worker_jobs.py:69-70`
**Issue:** The test comment at line 69 says "We never acquired the lock, so we must NOT call delete". The implementation at `jobs.py:107` calls `redis.delete(lock_key)` unconditionally in the `finally` block, even when the lock was never acquired. This means the test assertion at line 70 (`ctx["redis"].delete.assert_not_called()`) is testing implementation behaviour that currently passes only because the early `return` at line 84 exits before the `try` block is entered — so the `finally` never runs.

The test is correct and the code is correct, but the assertion is fragile: if someone moves the `return` statement inside the `try` block (a natural refactor), the test would start failing with a cryptic assertion error rather than a clear test failure. The test should be updated to document *why* `delete` is not called.

**Fix:**
```python
# More explicit assertion with explanation
ctx["redis"].delete.assert_not_called()  # early return before try block, so finally never runs
```

Or restructure the lock guard to only enter `try` after acquiring:
```python
if not acquired:
    log.warning("sync_task_lock_not_acquired", zoho_task_id=zoho_task_id)
    return

try:
    ...
finally:
    await redis.delete(lock_key)
```
The current code already has this structure (return is before try), so the test is correct — just add the explanatory comment.

---

_Reviewed: 2026-04-24T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
