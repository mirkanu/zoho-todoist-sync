---
quick_id: 260501-m0t
slug: remove-zoho-id-footer-from-todoist-task-
description: Remove [zoho:ID] footer from Todoist task descriptions
date: 2026-05-01
status: complete
commit: 383f270
---

# Summary

Removed the `[zoho:ZOHO_TASK_ID]` footer from Todoist task descriptions. The `sync_state` table's indexed `todoist_task_id` column already provides the bidirectional mapping, making the footer redundant.

## What changed

- **`app/todoist/writer.py`** — `create_todoist_task` no longer sets `description`; removed Pitfall-6 comment from `update_todoist_task`
- **`app/webhooks/router.py`** — `item:added` handler now discards unconditionally (no footer check, no enqueue); removed `extract_zoho_id` import and stale EDGE-8 comment
- **`app/todoist/normalise.py`** — Removed `extract_zoho_id` function and `ZOHO_ID_RE` import
- **`app/core/normalise.py`** — Removed `FOOTER_RE`, `ZOHO_ID_RE`, and `strip_footer`
- **`app/todoist/sync_manager.py`** — Removed footer-based item discard; `startup_sync` now counts all non-deleted items
- **`app/worker/reconciler.py`** — Removed EDGE-8 footer re-attachment block; Todoist delta sweep now uses `sync_state` DB lookup instead of footer parsing
- **`app/worker/jobs.py`** — Updated LOOP-5 docstring comment
- **`scripts/migrate.py`** — Removed SEED-3 footer-stamp (`update_task(description=footer)`) from the link-existing-pair path
- **`scripts/e2e_test.py`** — Replaced `find_todoist_task_for_zoho` (footer scan) with `get_todoist_task_for_zoho` (sync_state DB lookup)
- **Tests** — Removed all `extract_zoho_id`, `strip_footer`, `ZOHO_ID_RE` tests; updated webhook/reconciler/migration/writer tests accordingly

## Decisions

- `item:added` is discarded for all cases: echoes of our own Zoho→Todoist writes are suppressed by hash-match anyway; native Todoist tasks are not synced to Zoho in v1
- Existing Todoist tasks retain their old footer in the description — harmless since nothing reads it anymore; not worth a migration to strip it
