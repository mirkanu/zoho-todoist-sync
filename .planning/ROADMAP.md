# Roadmap: zoho-todoist-sync

## Milestones

- ✅ **v1.0 MVP** - Phases 1-8 (shipped 2026-05-01)
- 🚧 **v1.1 Nirvana Provider** - Phase 9 (in progress)

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1-8) - SHIPPED 2026-05-01</summary>

Two-way sync between Zoho CRM Tasks (assigned to me) and a Todoist project. 8 phases, 174 commits, 42 tasks migrated from Make.com. Original ROADMAP.md and REQUIREMENTS.md for this milestone were archived under `.planning/milestones/v1.0-*` but were later purged from git history (commit 991b280, 2026-05-30) for containing infra-specific details unsuitable for the public repo. See `CLAUDE.md` for the surviving architectural summary (stack, deployment, key facts, critical constraints).

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
- [ ] 09-05-PLAN.md — Rewire worker jobs, Todoist webhook route, and app/main.py through TaskProvider
- [ ] 09-06-PLAN.md — Generalize orphan_sweep to TaskProvider; add nirvana_poll_sweep cron function
- [ ] 09-07-PLAN.md — Wire arq worker settings (on_startup/on_shutdown/cron_jobs) through TaskProvider and register nirvana_poll_sweep

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → ... → 8 → 9

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|-----------------|--------|-----------|
| 1-8. (see CLAUDE.md) | v1.0 | 8/8 phases | Complete | 2026-05-01 |
| 9. Nirvana TaskProvider | v1.1 | 0/7 | Not started | - |
