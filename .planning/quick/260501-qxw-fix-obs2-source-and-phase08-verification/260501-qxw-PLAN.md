---
quick_id: 260501-qxw
slug: fix-obs2-source-and-phase08-verification
description: Fix OBS-2 source values + write Phase 08 VERIFICATION.md
date: 2026-05-01
---

# Quick Task 260501-qxw: Fix OBS-2 source values + write Phase 08 VERIFICATION.md

## Task 1: Thread source parameter through enqueue_sync → sync_task

**Files:** app/worker/enqueue.py, app/worker/jobs.py, app/webhooks/router.py, app/worker/reconciler.py, tests/unit/

**Action:**
1. enqueue.py: add `source: str = "worker"` param, forward to enqueue_job positionally
2. jobs.py: add `source="worker"` to sync_task + _execute_sync + _handle_new_task; replace hardcoded `source="worker"` literals with the variable
3. router.py: pass `source="zoho_webhook"` / `source="todoist_webhook"` at each enqueue_sync call site
4. reconciler.py: pass `source="reconciler"` at both enqueue_sync call sites in reconcile_sweep
5. Tests: assert source kwarg at relevant call sites

**Verify:** `pytest tests/ -x -q` green; `grep 'source="worker"' app/worker/jobs.py` empty

**Done:** All SyncEvent inserts in jobs.py use the `source` variable; each call site passes correct value

## Task 2: Write 08-VERIFICATION.md

**Files:** .planning/phases/08-observability-migration/08-VERIFICATION.md

**Action:** Create verification document recording Phase 8 completion — OBS-1/2/3/4 + SEED-1/2/3/4 all satisfied, commits referenced, live system metrics included.

**Verify:** File exists; all 8 req IDs present; status: passed in frontmatter

**Done:** 08-VERIFICATION.md exists and is complete
