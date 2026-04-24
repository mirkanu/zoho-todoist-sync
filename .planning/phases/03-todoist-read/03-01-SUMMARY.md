---
phase: 03-todoist-read
plan: "01"
subsystem: todoist-normalise
tags: [todoist, normalise, footer-parsing, tdd, sync-5, sync-8, sync-9]
dependency_graph:
  requires: [app/core/normalise.py]
  provides: [app/todoist/normalise.py]
  affects: [app/todoist/client.py, app/sync_manager.py]
tech_stack:
  added: []
  patterns: [pure-function-adapter, tdd-red-green, import-dont-redefine]
key_files:
  created:
    - app/todoist/__init__.py
    - app/todoist/normalise.py
    - tests/unit/test_todoist_normalise.py
  modified: []
decisions:
  - "Reuse ZOHO_ID_RE from app.core.normalise — adapter imports the compiled regex, does not redefine it"
  - "str(task.due.date) stringifies both date and datetime; normalise_due_date handles both via fromisoformat"
  - "MagicMock used for Task in tests — SDK dataclass has many required fields making direct construction impractical"
metrics:
  duration: "5 minutes"
  completed_date: "2026-04-24"
  tasks_completed: 2
  files_changed: 3
requirements: [SYNC-5, SYNC-8, SYNC-9]
---

# Phase 03 Plan 01: Todoist Normalise Layer Summary

**One-liner:** Pure-function footer parser and Task-to-NormalisedTask adapter with structural + behavioural SYNC-9 labels exclusion, 15 unit tests all green.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create app/todoist package + extract_zoho_id with full unit tests | 4eb660e | app/todoist/__init__.py, app/todoist/normalise.py, tests/unit/test_todoist_normalise.py |
| 2 | Add todoist_task_to_normalised adapter (SYNC-9 labels exclusion enforced) | 90ea323 | app/todoist/normalise.py, tests/unit/test_todoist_normalise.py |

## What Was Built

### app/todoist/__init__.py
Empty package marker. Mirrors app/zoho/__init__.py exactly.

### app/todoist/normalise.py
Two pure functions:

**`extract_zoho_id(description: str | None) -> str | None`**
- Uses `ZOHO_ID_RE` imported from `app.core.normalise` (no re.compile duplication)
- Handles: None, empty string, missing footer, footer at end, footer mid-text, post-user-edit footer
- Returns digits-only match as str, or None for non-digit/empty IDs (SYNC-5 tamper mitigation T-03-01-01)
- None return is the SYNC-8 precondition: caller discards footerless tasks

**`todoist_task_to_normalised(task: Task) -> NormalisedTask`**
- Maps `task.content` -> `normalise_title()` -> `title`
- Maps `task.due.date` -> `str()` -> `normalise_due_date()` -> `due_date` (handles both `date` and `datetime` SDK types)
- Passes `task.priority` through unchanged (already Todoist int 1..4)
- Maps `task.is_completed` (property: `completed_at is not None`) -> `is_completed`
- NEVER reads `task.labels` (SYNC-9 behavioural enforcement)
- Returns `NormalisedTask` which has no `labels` field (SYNC-9 structural enforcement)

### tests/unit/test_todoist_normalise.py
15 unit tests:
- 9 `test_extract_zoho_id_*`: None/empty/missing/footer-at-end/mid-text/post-edit/non-digit/empty-id/return-type
- 6 adapter tests: basic-date-only/datetime-due/no-due/completed/excludes-labels-structural/ignores-labels-behavioural

## Verification

```
pytest tests/unit/test_todoist_normalise.py -x -q
15 passed in 0.07s

pytest tests/ -q
139 passed in 2.13s
```

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. Pure functions only.

Threat mitigations implemented as planned:
- T-03-01-01 (Tampering): `ZOHO_ID_RE = r"\[zoho:(\d+)\]"` digits-only; `[zoho:abc]` and `[zoho:]` return None — covered by tests
- T-03-01-02 (Info Disclosure): NormalisedTask has no labels field (structural); adapter body never reads task.labels (behavioural); both asserted in tests
- T-03-01-03 (DoS): accepted — regex is linear, no nested quantifiers

## Known Stubs

None.

## Self-Check: PASSED

- app/todoist/__init__.py: FOUND
- app/todoist/normalise.py: FOUND (contains extract_zoho_id, todoist_task_to_normalised, imports ZOHO_ID_RE, no re.compile, no task.labels)
- tests/unit/test_todoist_normalise.py: FOUND (15 test functions)
- Commit 4eb660e: FOUND
- Commit 90ea323: FOUND
