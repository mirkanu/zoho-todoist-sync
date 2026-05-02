# zoho-todoist-sync

## What This Is

A scoped, loop-safe, two-way sync between Zoho CRM tasks (assigned to me) and a single Todoist project, running as a background service on Railway. Replaced a broken Make.com scenario and a failed zzzBots integration. No UI — just reliable, automatic task propagation.

## Core Value

All Zoho tasks assigned to me appear in Todoist within 60 seconds, and edits flow both ways without ever creating an infinite loop.

## Current State (v1.0 — Live)

**Status:** Production since 2026-05-01
**Sync state:** 42 tasks actively synced (migrated from Make.com)
**Make.com:** Replaced and decommissioned

### What's running

- `web` service: FastAPI on Railway, webhook endpoints for Zoho + Todoist
- `worker` service: arq on Railway, sync_task jobs + reconcile/orphan/daily_summary crons
- Postgres + Redis on Railway
- `/health` endpoint returns live status

### Known tech debt

| Item | Severity |
|------|----------|
| `sync_events.source` always `'worker'` (OBS-2 spec has richer enum) | Low |
| Resend sender `sync-alerts@resend.dev` placeholder — emails not delivering | Medium |
| Phase 07 missed-webhook E2E not formally re-run after deployment | Low |

---

## Next Milestone Goals (v1.1)

To be defined via `/gsd-new-milestone`. Candidates:

1. Fix OBS-2 source enum — thread correct `source` values from webhook handlers
2. Resend verified domain — get emails actually delivering
3. V2 Todoist content: include Zoho task link + Deal title (user-requested from Make.com)
4. Formal Phase 07 missed-webhook E2E re-run

---

## Requirements

See archived requirements: [`.planning/milestones/v1.0-REQUIREMENTS.md`](.planning/milestones/v1.0-REQUIREMENTS.md)

Fresh requirements for v1.1 will be created via `/gsd-new-milestone`.

---

## Context

- **History:** zzzBots sync broke ~4 weeks ago; vendor unresponsive. Make.com replacement was error-prone and incorrectly applied time-of-day to Zoho due dates when syncing to Todoist. Both prior implementations failed at loop prevention.
- **Loop prevention:** Canonical hash (SHA-256 over `{title, due_date, priority, is_completed}`). If incoming hash matches stored hash → echo suppressed, no write.
- **ID linkage:** `sync_state` DB row links `zoho_task_id ↔ todoist_task_id`. Zoho also stores Todoist ID in custom field `Todoist_Task_ID`. (Footer `[zoho:ID]` scheme was used in v1 but removed post-launch — sync_state is now sole linkage.)
- **Infrastructure:** Railway (same account as GSD Dashboard). Postgres + Redis on Railway.

## Constraints

- **Reliability:** Reliability > features. Daily-use work dependency.
- **Rate limits:** Zoho free tier — reconciliation uses incremental `Modified_Time` queries. Todoist Sync API — `sync_token` incremental updates; full sync only on startup.
- **No DB+API transaction:** Write to target API, then update sync_state. Partial failure handled by retry idempotency.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Canonical hash loop prevention | Hash normalised payload identically from either source; skip if hash matches. Solves echo at data layer. | Implemented Phase 01. 48 unit tests. |
| Footer `[zoho:ID]` → removed | Originally: footer in Todoist description as ID linkage. Removed post-v1: user edits could break it. sync_state DB column is now sole linkage. | Simpler, more robust. |
| Zoho custom field `Todoist_Task_ID` | Direct lookup without scanning descriptions; "already synced" marker. | Resolved at startup via field_label match. |
| Due date always date-only | Zoho may return datetime. Always normalise to `YYYY-MM-DD`. Fixes Make.com time-chip regression. | Phase 01: `normalise_due_date` uses `fromisoformat().date()`. |
| Todoist delete → Zoho delete | Treat as intent signal. Fire-and-forget Resend email. | Phase 04: 404 is idempotent. |
| per-call httpx.AsyncClient | ZohoClient holds mutable access_token; shared client would cache stale token. | Phase 02. |
| LWW conflict resolution | Last-write-wins on simultaneous edits; logged as `action='overwrite'`. Accept v1 data loss risk. | Phase 05. |

---

*Last updated: 2026-05-02 — v1.0 milestone archived*
