# Roadmap: zoho-todoist-sync

## Milestones

- ✅ **v1.0 MVP** - Phases 1-8 (shipped 2010-05-01)
- 🚧 **v1.1 Nirvana Provider** - Phase 9 (in progress)

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1-8) - SHIPPED 2010-05-01</summary>

Two-way sync between Zoho CRM Tasks (assigned to me) and a Todoist project. 8 phases, 174 commits, 42 tasks migrated from Make.com. Original ROADMAP.md and REQUIREMENTS.md for this milestone were archived under `.planning/milestones/v1.0-*` but were later purged from git history (commit 991b280, 2010-05-30) for containing infra-specific details unsuitable for the public repo. See `CLAUDE.md` for the surviving architectural summary (stack, deployment, key facts, critical constraints).

</details>

### 🚧 v1.1 Nirvana Provider (In Progress)

**Milestone Goal:** Support Nirvana (nirvanahq.com) as an alternative sync target to Todoist, selectable via config, without losing the ability to switch back to Todoist.

#### Phase 9: Nirvana TaskProvider
**Goal**: Replace Todoist with Nirvana as the active sync target, behind a `TaskProvider` abstraction that both Todoist and Nirvana implement — switching between them is a config change (`TASK_PROVIDER` env var), not a rewrite.
**Depends on**: Phase 1-8 (existing Zoho/Todoist sync architecture — sync_state table, arq worker, loop-prevention hash)
**Success Criteria** (what must be TRUE):
  1. A `TaskProvider` interface exists with a Todoist implementation (refactored from existing code) and a Nirvana implementation (new)
  2. Setting `TASK_PROVIDER=nirvana` routes all sync operations through Nirvana's MCP REST wrapper instead of the Todoist API, with no other code changes
  3. Nirvana tasks sync bidirectionally with Zoho: create, update (due date, tags/state, starred), complete, and the loop-prevention hash correctly derives `is_completed` from Nirvana's date-string `completed` field
  4. The Zoho picklist priority maps onto Nirvana's two independent axes (`state` + `starred`), not a single enum
  5. The worker polls Nirvana on an interval (hourly default, configurable) since Nirvana has no webhook equivalent
  6. Switching `TASK_PROVIDER` back to `todoist` restores the original working behavior with no data loss
**Plans**: 7 plans

Plans:
- [x] 09-01-PLAN.md — Settings fields (TASK_PROVIDER/NIRVANA_PAT/poll interval) + Todoist-int<->Nirvana two-axis priority mapping
- [x] 09-02-PLAN.md — sync_state schema: rename todoist_task_id -> external_task_id, add provider column (migration + backfill)
- [x] 09-03-PLAN.md — app/nirvana/ package: NirvanaClient, writer functions, normalise
- [x] 09-04-PLAN.md — TaskProvider Protocol + get_provider() factory; TodoistClient Protocol conformance
- [x] 09-05-PLAN.md — Rewire worker jobs, Todoist webhook route, and app/main.py through TaskProvider
- [x] 09-06-PLAN.md — Generalize orphan_sweep to TaskProvider; add nirvana_poll_sweep cron function
- [x] 09-07-PLAN.md — Wire arq worker settings (on_startup/on_shutdown/cron_jobs) through TaskProvider and register nirvana_poll_sweep

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → ... → 8 → 9

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|-----------------|--------|-----------|
| 1-8. (see CLAUDE.md) | v1.0 | 8/8 phases | Complete | 2010-05-01 |
| 9. Nirvana TaskProvider | v1.1 | 0/7 | Complete | 2026-07-25 |
| 10. Nirvana-initiated sync + notification batching | v1.1 | 0/8 | Not started | - |

#### Phase 10: Nirvana-initiated sync + notification batching
**Goal**: Close the propagation asymmetry that hid direction 4 from the 2026-10-21 live test (Nirvana creates a task → Zoho gets it too), and stop the per-row notification flood that produced 76 deletion emails in five minutes during the 2026-10-20 incident. Result: Nirvana can initiate any of the 4 task actions and have them land in Zoho; both `orphan_sweep` and `nirvana_poll_sweep` emit one email per cycle, not one per row.
**Depends on**: Phase 9 (Nirvana TaskProvider), quick task 261020-ljh (NirvanaClient cursor + 3-cycle confirm + kill-switch), quick task 261021-fix-e2e (live test wrapper that proves directions 1-3 green today).
**Success Criteria** (what must be TRUE):
  1. A Nirvana task created with no matching `sync_state` row is detected by `nirvana_poll_sweep`, gets a fresh Zoho task created, and both sides are linked via `sync_state` (zoho_task_id ↔ external_task_id ↔ provider='nirvana').
  2. A Nirvana task marked `done=True` propagates to Zoho as a terminal Status update (e.g. `Completed`), via the same loop-prevention-hash path used for ordinary updates.
  3. A Nirvana task moved to `state='trash'` deletes the linked Zoho task (verifies the existing path on rows that have a `sync_state` row; hardens the trash-edge for recently-completed tasks).
  4. `app/core/notifications.py` exposes a `send_cycle_summary(channel, lines, subject)` helper that fires ONE Resend email + ONE Telegram alert per call — accumulating rows during a cycle and emitting one batched email at the end.
  5. `app/worker/reconciler.py::orphan_sweep` collects every per-row deletion into one cycle-summary call, replacing the per-row `send_deletion_notification` invocations.
  6. `app/worker/reconciler.py::nirvana_poll_sweep` does the same — one cycle-summary email per sweep instead of per-row.
  7. `./scripts/run_live_e2e.sh` exits 0 against the live Hetzner infra, ending with `=== ALL 7 DIRECTION TESTS PASSED ===`.
  8. `ZTS_DISABLE_ORPHAN_SWEEP=` emptied in the shared env file and the worker is force-recreated — gated on (a) success criterion 7 green, (b) a real dry-run of `orphan_sweep` observing at least one Nirvana row surviving a full sweep cycle (no first-cycle-threshold alert fired).
**Plans**: 8 plans

Plans:
- [ ] 10-01-PLAN.md — Nirvana → Zoho CREATE: extend `nirvana_poll_sweep` so a fresh Nirvana task (no `sync_state` match) creates a linked Zoho task
- [ ] 10-02-PLAN.md — Nirvana → Zoho COMPLETE: sweep-driven Zoho terminal Status update when Nirvana's `done=True` and Zoho's Status is not already terminal
- [ ] 10-03-PLAN.md — Nirvana → Zoho DELETE hardening: verify trash-edge, ensure linked rows are not orphaned
- [ ] 10-04-PLAN.md — `send_cycle_summary` notification helper in `app/core/notifications.py` (Resend + Telegram)
- [ ] 10-05-PLAN.md — Refactor `orphan_sweep` to use `send_cycle_summary` (one email per cycle, table of decisions)
- [ ] 10-06-PLAN.md — Refactor `nirvana_poll_sweep` to use `send_cycle_summary` (one email per cycle, table of events)
- [ ] 10-07-PLAN.md — Extend `scripts/e2e_nirvana_test.py` directions 4-7 to match the new production paths; run wrapper green
- [ ] 10-08-PLAN.md — Lift `ZTS_DISABLE_ORPHAN_SWEEP` once 10-07 is green AND a dry-run confirms Nirvana row survives full sweep cycle
