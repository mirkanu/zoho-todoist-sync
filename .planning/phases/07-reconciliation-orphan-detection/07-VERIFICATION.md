---
phase: 07-reconciliation-orphan-detection
verified: 2026-04-25T00:00:00Z
status: human_needed
score: 3/4
overrides_applied: 0
deferred:
  - truth: "Reconciler last-run timestamp is updated in kv_store AND /health endpoint reflects reconciler.last_run and flags degraded if stale beyond 25 minutes"
    addressed_in: "Phase 8"
    evidence: "Phase 8 success criteria: 'GET /health returns within 100ms ... response includes ... reconciler.last_run'"
human_verification:
  - test: "Simulate webhook receiver down: stop the FastAPI web service, edit a Zoho task, wait up to 20 minutes, confirm sync_task is enqueued and the change appears in Todoist"
    expected: "Edit is picked up and synced within 20 minutes by reconcile_sweep alone, without any webhook delivery"
    why_human: "Requires a live Railway environment with real Zoho/Todoist API access and a controlled service outage — cannot verify programmatically"
---

# Phase 7: Reconciliation & Orphan Detection — Verification Report

**Phase Goal:** Missed webhooks, dropped jobs, and orphaned task pairs are detected and resolved automatically without human intervention
**Verified:** 2026-04-25T00:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Reconciliation cron runs every 15 minutes: fetches Zoho tasks modified in last 20 minutes and Todoist incremental delta; hash-mismatch tasks enqueued with dedup; sync_token updated after each poll | VERIFIED | `reconcile_sweep` in reconciler.py lines 32-104; `cron(reconcile_sweep, minute={0,15,30,45}, ...)` in settings.py line 119; `RECONCILE_LOOKBACK_MINUTES=20`; `fetch_tasks_modified_since` + `fetch_sync_delta`; `enqueue_sync(redis, ..., defer_secs=0)`; `save_sync_token` called; 10 passing unit tests |
| 2 | An edit made in Zoho while the webhook receiver was down is picked up and synced within 20 minutes via reconciliation sweep alone | ? HUMAN NEEDED | Code supports it (20-min lookback, hash-mismatch enqueue, cron schedule), but simulating a downed webhook receiver requires a live environment with real API access |
| 3 | Orphan sweep runs hourly: verifies both sides exist + assigned; single 404 logged and counted; second consecutive 404 triggers deletion, sync_state removal, orphan log, Resend email | VERIFIED | `orphan_sweep` in reconciler.py lines 107-224; two-cycle confirmation via `orphan_check_count`; `_handle_orphan` deletes counterpart task, adds `SyncEvent(action='orphan', source='reconciler')`; writers send Resend notification internally; `cron(orphan_sweep, minute={0}, ...)` in settings.py line 120; 9 passing unit tests |
| 4 | Reconciler last-run timestamp updated in kv_store after each sweep; /health reflects reconciler.last_run and flags degraded if stale | PARTIAL — see Deferred | kv_store write verified (reconciler.py lines 96-98, 220-222 write `reconciler_last_run` and `orphan_sweep_last_run`); /health endpoint is Phase 8 work (deferred) |

**Score:** 3/4 truths verified (SC-4 partial: kv write done, /health deferred to Phase 8)

### Deferred Items

Items not yet met but explicitly addressed in later milestone phases.

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | /health endpoint reflects reconciler.last_run and flags degraded if stale beyond 25 minutes | Phase 8 | Phase 8 success criteria: "GET /health returns within 100ms ... response includes ... reconciler.last_run" |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/worker/reconciler.py` | reconcile_sweep cron + KV constants | VERIFIED | 281 lines; exports `reconcile_sweep`, `orphan_sweep`, `_handle_orphan`, `KV_RECONCILER_LAST_RUN`, `KV_ORPHAN_SWEEP_LAST_RUN`, `RECONCILE_LOOKBACK_MINUTES` |
| `tests/unit/test_reconciler.py` | 10+ unit tests for reconcile_sweep and orphan_sweep | VERIFIED | 762 lines; 19 tests (10 reconcile + 9 orphan); all passing |
| `app/worker/settings.py` | WorkerSettings.cron_jobs with 2 entries | VERIFIED | cron_jobs list with reconcile_sweep (every 15 min, timeout=300) and orphan_sweep (hourly, timeout=600) |
| `tests/unit/test_worker_settings.py` | test_cron_jobs_registered | VERIFIED | test_cron_jobs_registered at line 325; passes (13 total in file) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `app/worker/reconciler.py` | `app/worker/enqueue.py::enqueue_sync` | direct call with `defer_secs=0` | WIRED | Lines 66, 91 — `enqueue_sync(redis, zoho_task_id, defer_secs=0)` |
| `app/worker/reconciler.py` | `app/todoist/sync_manager.py::load_sync_token,save_sync_token` | module-level import + call | WIRED | Lines 16, 72-73, 80-81 |
| `app/worker/reconciler.py` | `app/zoho/token_manager.py::upsert_kv` | module-level import + caller-commits pattern | WIRED | Lines 22, 97-98, 221-222 — `upsert_kv` called then `session.commit()` |
| `app/worker/reconciler.py::orphan_sweep` | `app/todoist/writer.py::delete_todoist_task` | function call with `(todoist_task_id, ctx['todoist_client']._api)` | WIRED | Line 244 |
| `app/worker/reconciler.py::orphan_sweep` | `app/zoho/writer.py::delete_zoho_task` | function call with `(zoho_task_id, access_token)` | WIRED | Lines 255-256 |
| `app/worker/reconciler.py::orphan_sweep` | `app/core/notifications.py::send_deletion_notification` | delegated to writers internally | WIRED | Writers call it internally (documented in 07-02-SUMMARY.md); `_handle_orphan` does not double-call |
| `app/worker/settings.py` | `app/worker/reconciler.py::reconcile_sweep,orphan_sweep` | `cron_jobs` class attribute | WIRED | Lines 34, 118-121 |

### Data-Flow Trace (Level 4)

Not applicable — reconciler.py is a worker cron function (not a UI rendering component). It produces side effects (enqueue jobs, DB writes) rather than rendering dynamic data.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 19 reconciler unit tests pass | `python3 -m pytest tests/unit/test_reconciler.py -q` | 19 passed | PASS |
| Cron registration test passes | `python3 -m pytest tests/unit/test_worker_settings.py::test_cron_jobs_registered -q` | 1 passed | PASS |
| Full test suite (260 tests) green | `python3 -m pytest tests/ -x -q` | 260 passed, 3 warnings | PASS |
| No crontab-string pitfall | `grep -E '"\\*/[0-9]+|crontab_string' app/worker/reconciler.py` | no matches | PASS |
| No get_task pitfall (must use fetch_todoist_task) | `grep "todoist_client.get_task" app/worker/reconciler.py` | no matches | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| SEED-5 | 07-01-PLAN.md | Reconciliation cron every 15 min; fetch Zoho modified-since + Todoist delta; enqueue on hash mismatch | SATISFIED | `reconcile_sweep` implemented; cron registered in settings.py; 10 tests pass |
| SEED-6 | 07-02-PLAN.md | Orphan sweep hourly; verify both sides exist + assigned; two-cycle confirmation | SATISFIED | `orphan_sweep` + `_handle_orphan` implemented; cron registered; 9 tests pass |
| SEED-7 | 07-01-PLAN.md | sync_token persisted in Postgres; fallback to "*" on missing | SATISFIED | `load_sync_token` / `save_sync_token` called; crash-safe (token saved before processing items); `test_sync_token_saved` passes |
| LOOP-3 | 07-01-PLAN.md, 07-02-PLAN.md | SELECT FOR UPDATE on sync_state in critical section; session.begin() for transactional writes | SATISFIED | SELECT FOR UPDATE in jobs.py (sync_task critical section); `session.begin()` used for all orphan_sweep row mutations (increment, reset, delete) |
| EDGE-1 | 07-02-PLAN.md | Task reassigned away in Zoho: delete Todoist counterpart | SATISFIED | Owner.id mismatch sets `zoho_missing=True`; `_handle_orphan` calls `delete_todoist_task`; `test_orphan_reassignment_detected` passes |
| EDGE-2 | 07-02-PLAN.md | Todoist task deleted: delete Zoho counterpart | SATISFIED | `TodoistNotFoundError` sets `todoist_missing=True`; `_handle_orphan` calls `delete_zoho_task`; `test_orphan_todoist_missing` passes |
| EDGE-5 | 07-02-PLAN.md | Two-cycle confirmation before orphan handling | SATISFIED | `orphan_check_count < 1` check at line 200; increment at line 211; `test_orphan_first_cycle` and `test_orphan_second_cycle_deletion` pass |
| EDGE-6 | 07-02-PLAN.md | Resend email failure does not roll back deletion | SATISFIED | Notifications sent internally by writers with their own try/except; `_handle_orphan` never double-calls and doesn't wrap deletion in notification try/except |
| EDGE-8 | 07-02-PLAN.md | Missing footer on healthy Todoist task: re-attach via update_task | SATISFIED | Lines 179-186; purely additive `description + suffix`; `test_refooter_missing_footer` passes |
| SYNC-10 | 07-01-PLAN.md | arq job dedup via `_job_id=f"sync:{zoho_task_id}"` — reconciler relies on this | SATISFIED | Reconciler calls `enqueue_sync(redis, zoho_task_id, defer_secs=0)` with no special dedup logic; relies on `enqueue_sync`'s `_job_id` mechanism; `test_reconcile_dedup` passes |

### Anti-Patterns Found

No anti-patterns detected.

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | No TODO/FIXME/placeholder comments found | — | — |
| — | — | No stub returns (null/empty) found | — | — |
| — | — | No crontab-string pitfall | — | — |
| — | — | No get_task pitfall (uses fetch_todoist_task) | — | — |

### Human Verification Required

#### 1. Missed-Webhook Recovery E2E Test

**Test:** On a live Railway deployment: stop or block the FastAPI web service (simulating webhook receiver down). Make an edit to a Zoho task that has a sync_state row. Wait up to 20 minutes. Confirm the edit was picked up by reconcile_sweep (check sync_events for action='sync' with source='reconciler', and verify the Todoist task reflects the change).

**Expected:** The edit appears in Todoist within 20 minutes via reconcile_sweep alone, with no webhook delivery involved.

**Why human:** Requires a live Railway environment with real Zoho/Todoist API credentials, an active DB with sync_state rows, and a controlled simulation of webhook receiver downtime. Cannot verify with unit tests or static analysis.

### Gaps Summary

No blocking gaps. All code artifacts are implemented, substantive, and wired. All 10 requirement IDs (SEED-5, SEED-6, SEED-7, LOOP-3, EDGE-1, EDGE-2, EDGE-5, EDGE-6, EDGE-8, SYNC-10) are satisfied.

One ROADMAP success criterion is split across phases: the kv_store write half of SC-4 is done in Phase 7; the /health endpoint half is Phase 8 work and is deferred there.

One ROADMAP success criterion (SC-2: missed-webhook E2E) requires live environment testing and is routed to human verification.

---

_Verified: 2026-04-25T00:00:00Z_
_Verifier: Claude (gsd-verifier)_
