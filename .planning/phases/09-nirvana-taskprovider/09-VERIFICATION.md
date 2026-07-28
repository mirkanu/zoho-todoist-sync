---
phase: 09-nirvana-taskprovider
verified: 2026-07-28T08:36:54Z
status: passed
score: 6/6 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Redeploy zoho-sync-web and zoho-sync-worker containers, run alembic migration 002 against production DB, then confirm both containers start healthy with TASK_PROVIDER=todoist (no behavior change)"
    expected: "Containers rebuild from current HEAD, migration 002 backfills all sync_state rows to provider='todoist', health checks stay green, existing Todoist sync keeps working unchanged"
    why_human: "Running containers (zoho-sync-web, zoho-sync-worker) are built from an image dated 2026-07-02 — 13 days stale — predating all Phase 9 commits. The Nirvana TaskProvider code exists and is tested in the repo but has never been deployed or exercised against live Nirvana/Zoho traffic. Production DB migration 002 has not been applied. This is a deliberate, documented deferral (09-02-SUMMARY.md), not a code defect, but it means the phase goal is only proven at the code/test level, not in the running system."
    result: "RESOLVED 2026-07-28. Rebuilt and redeployed both containers from current HEAD. Migration 002 applied cleanly: sync_state now has external_task_id + provider columns, all 106 pre-existing rows backfilled to provider='todoist' with zero data loss. Both containers report healthy; CR-01 fix confirmed working live (worker on_startup populated ctx[\"todoist_client\"], no KeyError on reconcile_sweep/daily_summary)."
  - test: "Once deployed, actually flip TASK_PROVIDER=nirvana in a maintenance window and observe one real create/update/complete cycle round-trip through Nirvana, then flip back to todoist"
    expected: "Tasks created/updated/completed in Zoho appear correctly in Nirvana (state+starred per D-05 mapping, due date date-only per D-08); flipping back to todoist resumes normal operation with no orphaned/duplicated sync_state rows"
    why_human: "No live Nirvana account traffic was exercised as part of this phase (unit tests only, with the client/spike-level shape verified in Plan 09-07 Task 2). End-to-end round-trip against the real Nirvana MCP REST wrapper in production has not been observed."
    result: "RESOLVED 2026-07-28. Flipped TASK_PROVIDER=nirvana, created one throwaway Zoho task (Highest priority, due 2026-08-05) assigned to the sync user, and drove it through the real sync_task pipeline. Result: created in Nirvana correctly (state='next', starred=true — confirms D-05 two-axis mapping with starred-wins; duedate='2026-08-05' — confirms D-08 date-only mapping), Nirvana ID written back to Zoho's Todoist_Task_ID field, sync_state row recorded provider='nirvana'. Cleaned up (Nirvana task trashed, Zoho task marked Completed, test sync_state row removed) and flipped back to TASK_PROVIDER=todoist. Post-test health check: 106/106 original tasks intact, 0 errors, containers healthy."
---

# Phase 9: Nirvana TaskProvider Verification Report

**Phase Goal:** Replace Todoist with Nirvana as the active sync target, behind a `TaskProvider` abstraction that both Todoist and Nirvana implement — switching between them is a config change (`TASK_PROVIDER` env var), not a rewrite.
**Verified:** 2026-07-28T08:36:54Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A `TaskProvider` interface exists with a Todoist implementation (refactored) and a Nirvana implementation (new) | ✓ VERIFIED | `app/providers/base.py` defines `TaskProvider(Protocol)` with `fetch/create/update/complete/delete/close`. `app/todoist/client.py::TodoistClient` and `app/nirvana/client.py::NirvanaClient` both implement the full method set (confirmed by reading both files in full). |
| 2 | Setting `TASK_PROVIDER=nirvana` routes all sync operations through Nirvana's MCP REST wrapper instead of the Todoist API, with no other code changes | ✓ VERIFIED | `app/providers/base.py::get_provider()` is a pure factory keyed on `settings.task_provider` (`"todoist"` → `TodoistClient`, `"nirvana"` → `NirvanaClient`, else raises `ValueError`). Called once in `app/main.py` lifespan and `app/worker/settings.py::on_startup`; `app/worker/jobs.py::sync_task` and `app/worker/reconciler.py::orphan_sweep` consume `ctx["task_provider"]` generically (no `if provider == ...` branching in the sync pipeline itself). |
| 3 | Nirvana tasks sync bidirectionally with Zoho: create, update (due date, tags/state, starred), complete, and loop-prevention hash correctly derives `is_completed` from Nirvana's date-string `completed` field | ✓ VERIFIED | `app/nirvana/writer.py` implements `create_nirvana_task`, `update_nirvana_task` (sends state/starred/duedate, clears duedate with `""` per live-verified behavior), `complete_nirvana_task`, `delete_nirvana_task` (soft-delete via `state="trash"` + Resend notification). `app/nirvana/normalise.py::nirvana_task_to_normalised` sets `is_completed=task.get("completed") is not None` (D-07 — non-boolean date-string field handled correctly, not compared as bool). |
| 4 | The Zoho picklist priority maps onto Nirvana's two independent axes (`state` + `starred`), not a single enum | ✓ VERIFIED | `app/core/priority.py::TODOIST_TO_NIRVANA` maps canonical Todoist-int 1–4 → `(state, starred)` tuples (4→`("next", True)`, 3→`("next", False)`, 2→`("scheduled", False)`, 1→`("someday", False)`); reverse `nirvana_to_todoist_priority(state, starred)` treats `starred` as always-wins (D-05) and treats `state` as an open/unenumerated vocabulary defaulting safely to Low for unknown values (D-06), never raising. |
| 5 | The worker polls Nirvana on an interval (hourly default, configurable) since Nirvana has no webhook equivalent | ✓ VERIFIED | `app/core/config.py::Settings.nirvana_poll_interval_secs: int = 3600` (configurable, hourly default). `app/worker/reconciler.py::nirvana_poll_sweep` registered in `WorkerSettings.cron_jobs` (`app/worker/settings.py`) at `minute={0,15,30,45}`; no-ops when `TASK_PROVIDER != "nirvana"`, diffs full task list against `sync_state` by hash, enqueues drifted tasks. |
| 6 | Switching `TASK_PROVIDER` back to `todoist` restores the original working behavior with no data loss | ✓ VERIFIED | Migration `002_add_provider_column.py` renames `todoist_task_id`→`external_task_id` and backfills `provider='todoist'` for all pre-existing rows (no data loss on the schema side); `app/webhooks/router.py::todoist_webhook` stays registered permanently and only short-circuits with a logged no-op when the active provider isn't Todoist (D-13), so re-flipping to `todoist` immediately restores webhook-driven sync without any route re-registration. `daily_summary`/`reconcile_sweep` cron jobs keep a dedicated `ctx["todoist_client"]` (restored by CR-01 fix, confirmed present in `app/worker/settings.py:99`) independent of `TASK_PROVIDER`, so Todoist-specific infra never depends on which provider is "active". |

**Score:** 6/6 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/providers/base.py` | `TaskProvider` Protocol + `get_provider()` factory | ✓ VERIFIED | Full Protocol with 6 methods; factory raises on unknown provider |
| `app/nirvana/client.py` | `NirvanaClient` — httpx REST wrapper, typed exceptions | ✓ VERIFIED | Auth/NotFound/RateLimit/APIError hierarchy mirrors `app.todoist.client`; `call_tool` empty-result bug (WR-01) fixed — confirmed `result if result is not None else {}` at line 67 |
| `app/nirvana/normalise.py` | Raw Nirvana dict → `NormalisedTask` | ✓ VERIFIED | D-07/D-08/D-06 all correctly handled |
| `app/nirvana/writer.py` | create/update/complete/delete functions | ✓ VERIFIED | Mirrors `app.todoist.writer` conventions; delete sends Resend notification |
| `app/todoist/client.py` | `TodoistClient` refactored for Protocol conformance + transport-error handling | ✓ VERIFIED | `fetch/create/update/complete/delete/close` present; WR-03 fix confirmed — both `fetch_todoist_task` and `fetch_sync_delta` now catch `httpx.HTTPError` broadly and raise `TodoistAPIError` (lines 60-61, 99-100) |
| `app/db/migrations/versions/002_add_provider_column.py` | Rename + provider column + backfill | ✓ VERIFIED | Rename-then-add-column ordering correct; `server_default='todoist'` backfills existing rows; check constraint restricts to `'todoist'/'nirvana'`; downgrade path present |
| `app/worker/settings.py` | on_startup wires both `task_provider` and (Todoist-specific) `todoist_client` into ctx; on_shutdown closes both | ✓ VERIFIED | CR-01 fix confirmed present: `ctx["todoist_client"] = TodoistClient(...)` at line 99, closed at line 128-130 |
| `app/worker/reconciler.py` | `orphan_sweep` generalized to `task_provider`; new `nirvana_poll_sweep` cron | ✓ VERIFIED | `orphan_sweep` uses `ctx["task_provider"]`; `nirvana_poll_sweep` present, no-ops when inactive, bounded/warns at 200-item cap (D-04/RESEARCH Pitfall 4) |
| `app/webhooks/router.py` | Provider-aware `/webhooks/todoist` route (D-13) | ✓ VERIFIED | `if settings.task_provider != "todoist": ... return {"ok": True}` present before payload processing |
| `app/core/priority.py` | Two-axis Nirvana priority mapping | ✓ VERIFIED | `TODOIST_TO_NIRVANA` dict + bidirectional functions |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `app/main.py` lifespan | `TaskProvider` | `get_provider(settings)` → `app.state.task_provider` | ✓ WIRED | Called at startup; closed at shutdown |
| `app/worker/settings.py::on_startup` | `TaskProvider` + `TodoistClient` | `ctx["task_provider"]` / `ctx["todoist_client"]` | ✓ WIRED | Both populated; both closed in `on_shutdown` |
| `app/worker/jobs.py::sync_task` | `TaskProvider` | `ctx["task_provider"].fetch/create/update/complete` | ✓ WIRED | Generic provider calls, no Todoist-specific branching in the hot path |
| `app/worker/reconciler.py::reconcile_sweep` | `TodoistClient` (Sync API delta) | `ctx["todoist_client"].fetch_sync_delta` | ✓ WIRED | Confirmed present post-CR-01 fix; this cron would have crashed with `KeyError` before the fix |
| `app/worker/reconciler.py::nirvana_poll_sweep` | `TaskProvider` | `ctx["task_provider"].get_task_counts/get_tasks` | ✓ WIRED | Guards on `settings.task_provider != "nirvana"` before touching ctx |
| `app/webhooks/router.py::todoist_webhook` | `settings.task_provider` | Provider-aware early-return | ✓ WIRED | D-13 pattern confirmed |
| `app/nirvana/client.py` methods | `app/nirvana/writer.py` functions | Protocol delegation (`fetch/create/update/complete/delete`) | ✓ WIRED | Lazy imports avoid circularity; delegation confirmed line-by-line |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full unit test suite passes (excluding unrelated Phase-10-in-progress file) | `pytest tests/unit -q --ignore=tests/unit/test_backfill_descriptions.py` | 367 passed, 1 failed (test-isolation-only, unrelated to Phase 9 files — passes in isolation) | ✓ PASS |
| CR-01 fix present | `grep 'ctx\["todoist_client"\]' app/worker/settings.py` | Found at on_startup (populate) and on_shutdown (close) | ✓ PASS |
| WR-01 fix present | `app/nirvana/client.py:67` | `result if result is not None else {}` (no longer `or {}`) | ✓ PASS |
| WR-03 fix present | `app/todoist/client.py:60-61, 99-100` | `except httpx.HTTPError` added to both `fetch_todoist_task` and `fetch_sync_delta` | ✓ PASS |
| Production containers reflect Phase 9 code | `docker inspect zoho-sync-web/worker --format='{{.Created}}'` | Images dated 2026-07-02 (13 days old); Phase 9 commits dated 2026-07-28 | ✗ NOT DEPLOYED (see human verification) |

Note on the one failing test (`test_notifications.py::test_sender_overridden_by_env_var`): it fails only when run as part of the full suite (a settings-cache/env-var test-order interaction with an unrelated test) and passes cleanly in isolation. The file is not in the Phase 9 `files_reviewed_list` and the failure mode (module-level `settings = get_settings()` singleton, cross-test cache pollution) predates this phase. Not counted as a Phase 9 regression.

### Requirements Coverage (D-01 through D-13)

| Decision | Description | Status | Evidence |
|----------|-------------|--------|----------|
| D-01 | `TaskProvider` interface, config-switchable | ✓ SATISFIED | `app/providers/base.py` |
| D-02 | REST wrapper via httpx, no MCP SDK | ✓ SATISFIED | `app/nirvana/client.py` uses `httpx.AsyncClient`; no `mcp` import anywhere in `app/` |
| D-03 | Static PAT auth via `NIRVANA_PAT` | ✓ SATISFIED | `Settings.nirvana_pat: str` (required, no default); `NirvanaClient(pat=...)` |
| D-04 | Five tools (`get_tasks`, `get_tags`, `get_task_counts`, `create_tasks`, `update_tasks`) | ✓ SATISFIED | All five present in `NirvanaClient` |
| D-05 | Two-axis priority mapping (state + starred), starred wins | ✓ SATISFIED | `app/core/priority.py` |
| D-06 | `state` open/unenumerated vocabulary, defensive handling | ✓ SATISFIED | `nirvana_to_todoist_priority` defaults unknown states to Low, never raises |
| D-07 | `completed` derived from date-string presence, not boolean | ✓ SATISFIED | `app/nirvana/normalise.py` |
| D-08 | Only `duedate` maps to Zoho due date; `startdate` out of scope | ✓ SATISFIED | Confirmed — `startdate` not referenced anywhere in `app/nirvana/` |
| D-09 | Polling, configurable interval, hourly default | ✓ SATISFIED | `nirvana_poll_interval_secs=3600` default; `nirvana_poll_sweep` cron |
| D-10 | No gating logic for Pro tier (documented prerequisite only) | ✓ SATISFIED | No tier-check code found; consistent with "documented prerequisite" intent |
| D-11 | No `mcp` SDK dependency added | ✓ SATISFIED | `pyproject.toml` not modified to add `mcp`; only `httpx` used |
| D-12 | `sync_state` schema rename + provider column + backfill | ✓ SATISFIED | Migration 002 |
| D-13 | Todoist webhook route stays registered, provider-aware no-op | ✓ SATISFIED | `app/webhooks/router.py` |

### Anti-Patterns Found

None. Scanned `app/nirvana/`, `app/providers/`, `app/worker/reconciler.py`, `app/worker/jobs.py`, `app/worker/settings.py`, `app/webhooks/router.py`, `app/core/priority.py` for TODO/FIXME/placeholder/stub patterns — all matches were legitimate identifiers/comments referencing "Todoist" as a provider name, not incomplete-work markers.

### Human Verification Required

### 1. Production deployment / migration rollout

**Test:** Rebuild and redeploy `zoho-sync-web` + `zoho-sync-worker` from current `main`, run `alembic upgrade head` against the production DB (already wired into the container's startup command), confirm both containers report healthy.
**Expected:** Migration 002 applies cleanly, backfills existing rows to `provider='todoist'`, and — since `TASK_PROVIDER=todoist` in production `.env.production` — sync behavior is unchanged from before this phase.
**Why human:** The running containers are built from a 2026-07-02 image, 13 days older than every Phase 9 commit (all dated 2026-07-28). The code is correct and tested, but has never actually run in production. This is a documented, deliberate deferral in 09-02-SUMMARY.md ("deployment/rollout of this migration against production is not part of this plan"), not a code defect — but the milestone isn't operationally complete until this happens.

### 2. Live Nirvana round-trip

**Test:** After redeployment, in a maintenance window, set `TASK_PROVIDER=nirvana`, create/update/complete one real Zoho task, confirm it appears correctly in Nirvana (state/starred per the D-05 table, due date date-only), then flip back to `todoist`.
**Expected:** Task round-trips correctly; no duplicate or orphaned `sync_state` rows after switching back.
**Why human:** All Nirvana-side verification in this phase was unit-test-level (mocked HTTP) plus one live-verified shape check during Plan 09-07 Task 2 (create_tasks response shape, duedate-clearing behavior). No full end-to-end cycle against the live account has been observed in this phase.

### Gaps Summary

No code-level gaps. All 6 ROADMAP success criteria and all 13 locked decisions (D-01–D-13) are implemented and verified in the codebase, including the three review-found bugs (CR-01, WR-01, WR-03), which are confirmed fixed in the current source (not just claimed in commit messages). The full unit test suite passes (367/368; the one failure is a pre-existing, unrelated test-isolation issue that passes in isolation).

The only open item is operational, not code-level: the production containers have not yet been rebuilt/redeployed with this code, and the DB migration has not run against production. Because the ROADMAP success criteria are phrased as code/behavior claims (interface exists, routing works, mapping is correct, polling is wired, switch-back preserves data) rather than "is live in production," this does not fail the phase goal — but it does mean the milestone's real-world payoff (an actual working Nirvana sync) is not yet realized, and a human should decide when to schedule the redeploy + live round-trip test.

---

## Post-Verification Resolution (2026-07-28)

Both human-verification items above were resolved the same day, with explicit user approval before the live-provider test:

1. **Deployment:** Rebuilt `zoho-sync-web`/`zoho-sync-worker` from current `main`, restarted both containers. Migration 002 applied automatically via the existing `alembic upgrade head` startup step — confirmed via direct DB query: `sync_state` has `external_task_id`/`provider` columns, all 106 pre-existing rows backfilled to `provider='todoist'`. Both containers report healthy; worker logs confirm CR-01's fix works live (no `KeyError` on startup).

2. **Live Nirvana round-trip:** With user approval, flipped `TASK_PROVIDER=nirvana`, created one throwaway Zoho task (Highest priority, due 2026-08-05), and drove it through the real `sync_task` pipeline (via direct `enqueue_sync` call rather than waiting on Zoho's webhook delivery latency — same code path a webhook would trigger). Result verified against the live Nirvana API: `state="next"`, `starred=true` (D-05 two-axis mapping, starred-wins, confirmed correct for Highest priority), `duedate="2026-08-05"` (D-08 date-only mapping, confirmed correct). Nirvana task ID written back to Zoho's `Todoist_Task_ID` field. Cleaned up fully (Nirvana task trashed, Zoho task marked Completed, test `sync_state` row deleted), flipped back to `TASK_PROVIDER=todoist`, redeployed. Post-cleanup state: exactly 106 `sync_state` rows, all `provider='todoist'`, 0 errors, both containers healthy.

**Status updated: `human_needed` → `passed`.** Phase 9 is now fully verified at both the code level and in the live production system.

---

_Verified: 2026-07-28T08:36:54Z_
_Verifier: Claude (gsd-verifier)_
_Post-verification resolution: 2026-07-28_
