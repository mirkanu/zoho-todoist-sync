# zoho-todoist-sync

## What This Is

A scoped, loop-safe, two-way sync between Zoho CRM tasks (assigned to me) and a single Todoist project, running as a background service on Railway. It replaces a broken Make.com scenario and a failed zzzBots integration. No UI — just reliable, automatic task propagation that can be forgotten about once it's running.

## Core Value

All Zoho tasks assigned to me appear in Todoist within 60 seconds, and edits flow both ways without ever creating an infinite loop.

## Requirements

### Validated

Validated in Phase 1 (Foundation):
- Project scaffold with pinned Python dependencies and fail-fast env validation
- Canonical hash stable across TZ offsets, Unicode forms, and line endings (LOOP-2)
- Priority mapping NOT inverted: Highest→4, High→3, Normal→2, Low/unset→1 (SYNC-2)
- NormalisedTask has exactly 4 fields — no description, no labels in sync scope (SYNC-7, SYNC-9)
- Zoho ID footer regex `[zoho:ID]` extracted at module level (SYNC-5)
- Postgres schema: sync_state, sync_events, kv_store tables with indexes and async Alembic migration (INFRA-1, INFRA-2)
- Fail-fast Settings class raises ValidationError on missing required env vars (INFRA-5)

Validated in Phase 3 (Todoist Read):
- `[zoho:ID]` footer extraction robust to None, empty, missing, and non-digit IDs via `extract_zoho_id()` (SYNC-5)
- Footerless Todoist items discarded at startup sync — `extract_zoho_id() is None` → log + skip (SYNC-8)
- Labels structurally excluded from sync path — `NormalisedTask` has no labels field; adapter never reads `task.labels`; enforced by two separate unit tests (SYNC-9)
- `sync_token` persisted to `kv_store` before item processing loop for crash-safety; `"*"` sentinel triggers full sync on first run (SEED-7)
- `startup_sync` wired into FastAPI lifespan before `yield`; `TodoistClient` lifecycle managed (open/close) around lifespan

Validated in Phase 4 (Write Operations):
- Todoist write path: create/update/complete/delete with correct payload shapes — `due_date` always a `date` object, never `due_datetime`; `due_string="no date"` to clear; `description`/`labels` never passed on update (SYNC-1, SYNC-2, SYNC-8)
- `[zoho:ID]` footer appended to description on Todoist task creation (SYNC-5)
- Zoho write path: update/complete/delete/write-todoist-id with reverse-mapped Priority strings; Due_Date=null serialises to JSON null (not omitted) (SYNC-3, EDGE-3)
- `write_todoist_id_to_zoho` uses dynamically-resolved `zoho_field_cache['todoist_task_id_api_name']` — not hardcoded (SYNC-6)
- `complete_zoho_task` uses `settings.zoho_terminal_statuses_list[0]` — not hardcoded "Completed" (EDGE-4)
- Todoist delete + Zoho delete both send Resend fire-and-forget email to manuelkuhs@gmail.com; 404 on delete is idempotent (no email) (EDGE-1, EDGE-2, EDGE-6)
- `complete_todoist_task` calls SDK `complete_task` → POST /tasks/{id}/close (EDGE-7)
- `resend.api_key` initialised exactly once in FastAPI lifespan in `app/main.py` (INFRA)
- 207 Zoho Multi-Status responses inspected per-record; error status raises ZohoAPIError (Pitfall 2)
- `write_todoist_id_to_zoho` raises ZohoAPIError before HTTP call if field_api_name unresolved (SYNC-6)

Validated in Phase 2 (Zoho Read):
- Proactive OAuth token refresh every 50 min; re-raises on failure (INFRA-6)
- Startup field metadata resolution — `api_name` for `Todoist Task ID` custom field resolved from Zoho API and cached (INFRA-7)
- Typed exceptions (ZohoAuthError, ZohoNotFoundError, ZohoRateLimitError) for all non-2xx Zoho responses (SYNC-4)
- Paginated `fetch_tasks_modified_since` always includes `Modified_Time` filter — no full scans (SYNC-4)
- `zoho_record_to_normalised` adapter reuses Phase 1 normalisation helpers (SYNC-4)
- `zoho_job_defer_secs` constant exposed in Settings for Phase 5 (LOOP-4)

### Active

- [ ] All open Zoho CRM tasks assigned to me appear in the target Todoist project within 60s of a change in Zoho
- [ ] Edits to title, due date, description, priority, or status in either system propagate to the other within 60s
- [ ] Zero infinite loops under any edit pattern (including rapid alternating edits)
- [ ] Todoist labels added locally are never echoed to Zoho
- [ ] Zoho due dates are always stripped to date-only in Todoist (no time component)
- [ ] Completing a task in either system completes it in the other
- [ ] A task reassigned away from me in Zoho is deleted from Todoist within 15 min; email notification sent via Resend
- [ ] Deleting a Todoist task (not completing) deletes the corresponding Zoho task; email notification sent via Resend
- [ ] New tasks created in Todoist do NOT create Zoho tasks (creation is Zoho → Todoist only)
- [ ] A `/health` endpoint returns last sync timestamp, 24h error count, and queue depth
- [ ] A daily Todoist task is auto-created summarising: N syncs, M errors, P echoes suppressed

### Out of Scope

- New Todoist tasks creating Zoho tasks — creation is deliberately one-way; Zoho is the source of truth for task creation
- Syncing Zoho tasks assigned to others — only my tasks are relevant
- Zoho Projects integration — different module, different complexity; possible Phase 2 project
- UI or admin dashboard — background service only; health endpoint is sufficient
- Backfilling completed or historical tasks — v1 seeds only currently-open tasks
- Recurring task recurrence mapping across systems — cross-system recurrence is complex enough to be its own project
- Zoho Projects (separate from CRM Tasks) — out of scope for this project

## Context

- **History:** zzzBots sync broke ~4 weeks ago; vendor unresponsive. Make.com replacement is error-prone and incorrectly applies time-of-day to Zoho due dates when syncing to Todoist. Both prior implementations failed at loop prevention.
- **Loop prevention:** Previous implementations struggled with infinite loops. This project encodes an explicit solution: a canonical hash computed identically from either system; if the incoming hash matches the stored hash, the event is a sync echo and is skipped.
- **Infrastructure:** Runs on Railway (same account as GSD Dashboard). Postgres + Redis already available on Railway. Deployment model is the same as existing Railway services.
- **Resend:** YNAB project on the same account already uses Resend (v6.9.4). API key needs to be retrieved from Railway and added as env var for this service.
- **Zoho webhook payload:** Zoho workflow webhooks deliver a notification (task ID + module), not the full task payload. The sync worker must fetch the full task from the Zoho API on dequeue — this is intentional and must not be short-circuited.
- **Todoist ID storage:** Zoho task ID stored as a footer in the Todoist task description: `\n\n---\n[zoho:1234567890]`. Regex-parseable, survives user edits above the footer. Labels rejected as metadata storage (user's label space must stay clean).
- **Zoho task ID storage:** Custom field `Todoist_Task_ID` on the Zoho Task module (single-line text, hidden from standard views).

## Constraints

- **Reliability:** Reliability > features. This is a daily-use work dependency. Correctness and loop safety take priority over scope.
- **Tech stack:** Python 3.12 + FastAPI + arq (Redis-backed async jobs) + Postgres on Railway. Zoho's official Python SDK is more complete than the Node one; `todoist-api-python` is mature.
- **Rate limits:** Zoho free tier API limits are tight — reconciliation must use incremental `Modified_Time` queries, not full scans. Todoist Sync API: 450 req/15 min per user — use `sync_token` incremental updates, full sync only on startup.
- **Idempotency:** Webhook delivery is at-least-once in both systems. All write paths must be idempotent via canonical hash check.
- **No DB+API transaction:** Write to target API, then update sync_state. Partial failure (API write succeeds, DB update fails) is handled by retry idempotency, not transactions.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Canonical hash loop prevention | Hash the normalised payload identically from either source; skip if incoming hash matches stored hash. Solves the echo problem at the data layer, not the timing layer. | Implemented in Phase 01: SHA-256 over JSON(sort_keys=True), stable across TZ/Unicode/CRLF. 48 unit tests pass. |
| Footer `[zoho:ID]` in Todoist description | Keeps Zoho task ID in Todoist without touching user's label space. Regex-parseable, survives user edits above the footer. | Implemented in Phase 01: FOOTER_RE and ZOHO_ID_RE exported from normalise.py. |
| Zoho custom field `Todoist_Task_ID` | Direct lookup without scanning descriptions; also acts as "already synced" marker. | Resolved in Phase 02: `api_name` resolved at startup via field_label match ("Todoist Task ID") against `GET /settings/fields?module=Tasks`; cached in `zoho_field_cache`. |
| Due date always date-only | Zoho API may return `YYYY-MM-DDTHH:MM:SS+offset` even for date-only fields. Always normalise to `YYYY-MM-DD`, never pass `due_datetime` to Todoist. Fixes the time-chip regression. | Implemented in Phase 01: normalise_due_date uses fromisoformat().date(); returns None for unparseable input (stricter than plan's raw[:10] fallback). |
| Todoist delete → Zoho delete | Treat Todoist deletion as an intent signal, not just local cleanup. Sends email notification via Resend. Accepted risk: Todoist deletions are hard to recover from. | Implemented in Phase 04: `delete_todoist_task` and `delete_zoho_task` both send Resend fire-and-forget email; 404 is idempotent (no email). |
| Reassignment → Todoist delete + email | Task reassigned away in Zoho means it's no longer my responsibility; remove from Todoist and notify via Resend. | Implemented in Phase 04: same `delete_todoist_task` path handles reassignment-triggered deletes. |
| LWW conflict resolution with logging | Last-write-wins on simultaneous edits, logged in sync_events with `action='overwrite'`. Simple for v1; inspect if it causes data loss. | — Pending |
| arq over Celery | Lighter weight for this workload; Redis already required for state; arq job dedup by key handles concurrent webhook races for the same task. | — Pending |
| Python over Node | Zoho official Python SDK is more complete; `todoist-api-python` is mature. | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-24 after Phase 4 (Write Operations) complete*
