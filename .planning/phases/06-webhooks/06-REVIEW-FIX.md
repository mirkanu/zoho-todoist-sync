---
phase: 06-webhooks
fixed_at: 2026-04-24T20:56:17Z
review_path: .planning/phases/06-webhooks/06-REVIEW.md
iteration: 1
findings_in_scope: 3
fixed: 3
skipped: 0
status: all_fixed
---

# Phase 06: Code Review Fix Report

**Fixed at:** 2026-04-24T20:56:17Z
**Source review:** .planning/phases/06-webhooks/06-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 3 (WR-01, WR-02, WR-03)
- Fixed: 3
- Skipped: 0

## Fixed Issues

### WR-01: Background refresh task leaked when `startup_sync` raises

**Files modified:** `app/main.py`
**Commit:** 79b3235
**Applied fix:** In the `except Exception` block of the `startup_sync` try/except, added `refresh_task.cancel()` followed by `await refresh_task` (suppressing `CancelledError`) before re-raising. This ensures the proactive refresh background task is stopped before the lifespan context propagates the startup failure.

---

### WR-02: Silent discard on missing `project_id` in Todoist payload

**Files modified:** `app/webhooks/router.py`
**Commit:** f62f2c2
**Applied fix:** Replaced the single `if event_data.get("project_id") != settings.todoist_project_id` guard with a two-step check: first guard for `raw_project_id is None` (emits `log.warning("todoist_event_missing_project_id", ...)`) and a second guard using `str(raw_project_id) != str(settings.todoist_project_id)` (preserving the existing DEBUG log for wrong-project events). This ensures missing `project_id` is surfaced as a warning rather than silently discarded, and integer `project_id` values compare correctly against the string config value.

---

### WR-03: Relative path in test reads source file — breaks when run from non-root directory

**Files modified:** `tests/unit/test_webhooks.py`
**Commit:** a48b87d
**Applied fix:** Replaced `pathlib.Path("app/webhooks/router.py").read_text()` with `(pathlib.Path(__file__).parents[2] / "app" / "webhooks" / "router.py").read_text()`. The path is now anchored to the test file's location, making it invariant to the working directory from which pytest is invoked.

---

_Fixed: 2026-04-24T20:56:17Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
