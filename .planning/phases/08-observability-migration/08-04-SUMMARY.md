---
phase: 08-observability-migration
plan: 04
subsystem: scripts
tags: [e2e, testing, scripts, manual-gate, SEED-4]

dependency_graph:
  requires:
    - scripts/migrate.py (bootstrap pattern)
    - app/zoho/writer.py (_auth_headers, update_zoho_task, complete_zoho_task, delete_zoho_task)
    - app/zoho/client.py (ZOHO_EU_BASE_URL, ZohoClient)
    - app/todoist/client.py (TodoistClient)
    - app/db/models.py (SyncEvent)
    - app/zoho/state.py (token_state, zoho_field_cache)
    - app/zoho/token_manager.py (KV_ACCESS_TOKEN_KEY, KV_EXPIRES_AT_KEY, load_token_from_kv, refresh_access_token, upsert_kv)
  provides:
    - scripts/e2e_test.py (standalone E2E harness — manual pre-migration gate)
  affects: []

tech_stack:
  added: []
  patterns:
    - asyncio polling loop with time.monotonic() deadline
    - try/finally unconditional cleanup (Pitfall 4)
    - httpx.AsyncClient for Zoho task creation
    - Bootstrap mirrors migrate.py (engine/session/token/field-cache)

key_files:
  created:
    - path: scripts/e2e_test.py
      role: "Standalone async E2E test harness — manual pre-migration gate per SEED-4"
      lines: 245
  modified: []

decisions:
  - "Used time.monotonic() for polling deadline rather than asyncio event_loop.time() per plan spec"
  - "Completion verification checks that active task list no longer contains the task (disappearance = closed)"
  - "Script is NOT in pytest suite per D-04 — invoked directly as python scripts/e2e_test.py"

requirements_satisfied:
  - SEED-4

metrics:
  duration_minutes: 5
  completed_date: "2026-04-25"
  tasks_completed: 1
  tasks_pending: 1
  files_created: 1
  files_modified: 0
---

# Phase 8 Plan 04: E2E Test Harness (SEED-4 Gate) Summary

**One-liner:** Standalone async E2E harness that drives create→edit(subject/due/priority)→complete→cleanup through the live Zoho→Todoist sync pipeline with 5s/90s polling and unconditional try/finally cleanup.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Implement scripts/e2e_test.py | 9b3734c | scripts/e2e_test.py (created, 245 lines) |

## Task 2: Awaiting Human Verification

Task 2 is a `checkpoint:human-verify` gate (SEED-4 blocking gate). The operator must run the live E2E test before proceeding to migration.

**Command:**
```
cd /data/home/zoho-todoist-sync
python scripts/e2e_test.py
```

**Pre-conditions:**
1. Web service AND worker service must be running (locally or on Railway) with the same DATABASE_URL and REDIS_URL the script uses
2. Zoho webhook must point to the running web service's `/webhooks/zoho` endpoint
3. `.env` file must contain all required env vars (see `app/core/config.py:Settings`)

**Expected output sequence (success):**
- `[E2E] Creating Zoho task subject='E2E test {ts}' ...`
- `[E2E] Zoho task created: {zoho_id}`
- `[E2E] Waiting up to 90s for Todoist propagation ...`
- `[E2E] Todoist task linked: {todoist_id}`
- `[E2E] Subject propagation verified`
- `[E2E] Due date propagation verified`
- `[E2E] Priority propagation verified`
- `[E2E] Completion propagation verified`
- `[E2E] sync_events count for test task: {N}` (N typically 4–8)
- `[E2E] PASS — full sync round-trip succeeded`
- `[E2E] Cleanup phase ...`
- `[E2E] Deleted Todoist task {todoist_id}`
- `[E2E] Deleted Zoho task {zoho_id}`
- Exit code 0

**Post-run verification:**
1. Confirm in Zoho UI that no task with subject containing "E2E test" remains
2. Confirm in Todoist UI that no task with `[zoho:...]` footer for the test ID remains

## Verification Results

- `python3 -m py_compile scripts/e2e_test.py` — PASS
- `from scripts.e2e_test import run_e2e, main, create_zoho_test_task, poll_until` — PASS (with env vars)
- `pytest tests/ -x -q` — PASS (285 tests, no regressions)
- All acceptance criteria grep checks — PASS
- File line count: 245 (>= 200 minimum)

## Deviations from Plan

None — plan executed exactly as written. The script body is a direct implementation of the pseudocode provided in the plan's `<action>` block.

## Threat Model Compliance

| Threat ID | Category | Mitigation | Status |
|-----------|----------|------------|--------|
| T-08-15 | Tampering (stale artifacts) | try/finally in run_e2e tracks zoho_id and todoist_id from moment of creation; each cleanup branch has its own try/except | Implemented |
| T-08-16 | Tampering (production exercise) | Tasks named `E2E test {timestamp}` — searchable and operator-owned | Accepted |
| T-08-17 | Info Disclosure (tokens in logs) | Only print() of operator messages; no token interpolation | Implemented |
| T-08-18 | DoS (Todoist rate limits) | 5s×5steps×18polls=450 calls max — well under limits; single human-gated run | Accepted |

## Known Stubs

None — script is not wired into automated CI and has no stubs. It is a manual gate by design (D-04).

## Self-Check: PASSED

- scripts/e2e_test.py: FOUND
- Commit 9b3734c: FOUND (git log confirms)
- 285 tests passing: CONFIRMED
