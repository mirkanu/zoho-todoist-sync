# Requirements

**Project:** zoho-todoist-sync
**Written:** 2026-04-23
**Status:** Draft — awaiting roadmap creation

> This document records every requirement with a stable REQ-ID. IDs never change. Requirements may be moved to Validated or Out of Scope as phases complete.

---

## Status Key

- **Active** — scoped, expected in v1
- **Validated** — implemented and verified
- **Out of Scope** — explicitly excluded (with reason)

---

## Infrastructure (INFRA)

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| INFRA-1 | Service runs as two Railway services: `web` (FastAPI) and `worker` (arq). Separate services for crash isolation — worker crash does not take down webhook ingestion. | Active | |
| INFRA-2 | Postgres database on Railway. Schema: `sync_state` (one row per linked task pair) and `sync_events` (append-only audit log). Indexes on `sync_state.todoist_task_id`, `sync_events(created_at DESC)`, `sync_events(zoho_task_id, created_at DESC)`. | Active | |
| INFRA-3 | Redis on Railway. Used by arq for job queue and deduplication. | Active | |
| INFRA-4 | Python 3.12, FastAPI, arq (Redis-backed async job queue), `todoist-api-python`, Zoho official Python SDK. | Active | |
| INFRA-5 | All secrets as Railway environment variables: `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`, `ZOHO_USER_ID`, `ZOHO_REGION` (default `eu`), `ZOHO_TODOIST_TASK_ID_FIELD` (actual API name of the custom field — read from Zoho settings at startup and cached), `TODOIST_API_TOKEN`, `TODOIST_PROJECT_ID` (value: `6gCPcWwM392GhXQh`), `TODOIST_CLIENT_SECRET` (for HMAC webhook verification), `RESEND_API_KEY`, `DATABASE_URL`, `REDIS_URL`. | Active | |
| INFRA-6 | Zoho OAuth: Self Client app, EU region (`accounts.zoho.eu`, `www.zohoapis.eu`). Access token auto-refreshed proactively at 50 min (tokens expire at 60 min). Refresh token stored in env var; if rotation is needed, document the manual recovery step. | Active | Zoho org confirmed EU from `crm.zoho.eu` URL |
| INFRA-7 | On service startup: read Zoho field metadata (`GET /crm/v6/settings/fields?module=Tasks`) to determine the actual `api_name` for the `Todoist Task ID` custom field. Cache as `ZOHO_TODOIST_TASK_ID_FIELD`. Field already exists (created by Make.com) and is already populated. Do not assume `Todoist_Task_ID` — verify. | Active | Field confirmed to exist via Make.com scenario blueprint |

---

## Core Sync (SYNC)

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| SYNC-1 | All open Zoho CRM tasks assigned to the configured user (`ZOHO_USER_ID`) appear in the target Todoist project within 60 seconds of a Zoho webhook firing. | Active | |
| SYNC-2 | Zoho → Todoist field mapping: `Subject` → `content`, `Due_Date` (date-only, always normalised to `YYYY-MM-DD`, never `due_datetime`) → `due.date`, `Priority` → `priority` (mapping: Highest→4, High→3, Normal→2, Low→1, Lowest→1, unset→1), `Status=Completed` → close task via `POST /tasks/{id}/close`. | Active | Priority mapping confirmed correct via research; brief had it inverted |
| SYNC-3 | Todoist → Zoho field mapping: `content` → `Subject`, `due.date` (date-only) → `Due_Date`, `priority` → `Priority` (reverse: 4→Highest, 3→High, 2→Normal, 1→Low), task completion (`item:completed`) → `Status="Completed"`. | Active | |
| SYNC-4 | Zoho webhook payload is notification-only (contains `module` + `ids`, not field values). Worker must fetch full task from Zoho API on dequeue. Never short-circuit by using webhook payload fields. | Active | Confirmed in PROJECT.md and research |
| SYNC-5 | Todoist task description format: `\n\n---\n[zoho:{ZOHO_TASK_ID}]` appended as a footer. This is the ID linkage and must survive user edits to the description body above the footer. Regex for extraction: `\[zoho:(\d+)\]`. | Active | |
| SYNC-6 | Zoho custom field `Todoist Task ID` (API name: verify at startup, likely `Todoist_Task_ID`) holds the Todoist task ID as a string. Already exists and already populated by Make.com for all previously-synced tasks. | Active | Confirmed via Make.com scenario inspection |
| SYNC-7 | Description sync is OUT of v1. The Todoist description field is used ONLY for the `[zoho:ID]` footer and the (legacy Make.com) preamble during migration. Do not include description in field mapping or canonical hash. | Active | Explicitly excluded by user |
| SYNC-8 | New tasks created natively in Todoist (without a `[zoho:ID]` footer) are ignored by the sync worker. The `item:added` event handler checks for footer presence before processing; tasks without the footer are logged and discarded. | Active | |
| SYNC-9 | Todoist labels added locally are never propagated to Zoho and are never cleared by the sync service. The canonical hash excludes the `labels` field. | Active | |
| SYNC-10 | arq job deduplication: enqueue with `_job_id=f"sync:{zoho_task_id}"`. If a job for the same task is already queued or in-progress, the duplicate is dropped. Log `WARN` on drop. The reconciliation sweep catches any missed change within 15 minutes. | Active | |
| SYNC-11 | LWW (last-write-wins) conflict resolution on simultaneous edits. Log the overwrite in `sync_events` with `action='overwrite'`. Accept the data loss risk for v1. | Active | |

---

## Loop Safety (LOOP)

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| LOOP-1 | Canonical hash loop prevention. For each linked task, compute a hash of the normalised sync payload: `sha256({title, due_date, priority, status})`. Store as `sync_state.last_hash`. On incoming change, compute hash from the incoming data; if it matches `sync_state.last_hash`, skip the write (echo suppressed). Log `action='echo_suppressed'` in `sync_events`. | Active | Description excluded from hash per SYNC-7 |
| LOOP-2 | Canonical hash normalisation rules: (a) due_date always `YYYY-MM-DD` string or `null`; (b) priority always Todoist integer (1–4); (c) title stripped of leading/trailing whitespace; (d) status as boolean `is_completed`; (e) NO metadata fields (timestamps, owner, labels, system fields). Normalise BEFORE hashing from either system. | Active | Normalisation edge cases documented in ARCHITECTURE.md |
| LOOP-3 | `SELECT ... FOR UPDATE` on `sync_state` row at the start of each sync job's critical section. Prevents two concurrent workers from both deciding to write for the same task. Defence-in-depth given arq job-ID dedup already prevents most concurrency. | Active | |
| LOOP-4 | 2-second deferred start on Zoho-triggered jobs (configurable via `ZOHO_JOB_DEFER_SECS`, default 2). Reduces race window between Zoho's internal write and its webhook delivery. | Active | |
| LOOP-5 | Bootstrap race mitigation: when this service creates a new Todoist task, Todoist fires `item:added`. The handler must check for the `[zoho:ID]` footer (or a `sync_state` lookup) BEFORE deciding whether to treat the event as a new Todoist-native task. Since the footer is injected on creation, the task will be identified as a sync-managed task and suppressed correctly. | Active | |

---

## Edge Cases (EDGE)

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| EDGE-1 | Task reassigned away from me in Zoho: delete the corresponding Todoist task and send a Resend email notification to `manuelkuhs@gmail.com`. Remove the `sync_state` row. Log `action='orphan'`. | Active | |
| EDGE-2 | Todoist task deleted (not completed) by user: delete the corresponding Zoho task and send a Resend email notification. Remove the `sync_state` row. Log `action='orphan'`. Todoist deletion is signalled by `is_deleted: true` in the Sync API delta or `item:deleted` webhook event. | Active | User accepted deletion propagation risk |
| EDGE-3 | Zoho task `due` field cleared: propagate `null` due date to Todoist (use Todoist REST API `due_date: null` to clear it). When Todoist `due` is `null` (not an object), propagate `null` to Zoho `Due_Date`. | Active | |
| EDGE-4 | Zoho `Status` picklist is org-configurable. Terminal statuses that trigger Todoist completion are configurable via `ZOHO_TERMINAL_STATUSES` env var (comma-separated, default `Completed`). Load and cache at startup. | Active | |
| EDGE-5 | Orphan detection: two-cycle confirmation before acting. A single 404 from Zoho (could be permissions, not deletion) does not trigger orphan handling. After a second 404 in the next reconciliation cycle, treat as genuine orphan. | Active | |
| EDGE-6 | Resend email failure does not roll back the deletion. Log the failure in `sync_events` with `action='error'`. Accept "deleted but no email" as the v1 failure mode. | Active | |
| EDGE-7 | Zoho task completion triggers Todoist `close`. Todoist task completion fires `item:completed` webhook — the handler must propagate to Zoho `Status=Completed` and log `echo_suppressed` on the resulting Zoho webhook. | Active | |
| EDGE-8 | Missing `[zoho:ID]` footer on a Todoist task that has a `sync_state` row (footer accidentally deleted by user): log `WARN`, treat as orphan in the next reconciliation cycle. The reconciler will detect the missing link and attempt re-footer via update. | Active | |

---

## Migration / Seed (SEED)

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| SEED-1 | **Migration mode, not fresh seed.** All currently-open Zoho tasks assigned to me already have Todoist counterparts created by Make.com, with `Todoist_Task_ID` populated in Zoho. The migration script links these existing pairs — it does NOT create new Todoist tasks for already-synced tasks. | Active | Confirmed via Make.com scenario inspection |
| SEED-2 | Migration script algorithm: (1) Fetch all open Zoho tasks assigned to me. (2) For each task where `Todoist_Task_ID` is set: fetch the Todoist task by that ID, update its description to append the `[zoho:ID]` footer (replacing the Make.com preamble), compute and store the canonical hash in `sync_state`. (3) For tasks where `Todoist_Task_ID` is empty: create in Todoist (with footer), write Todoist ID back to Zoho `Todoist_Task_ID` field, store in `sync_state`. | Active | |
| SEED-3 | Description migration: existing Todoist tasks from Make.com have descriptions in the format `Re: {related_to}\n[Zoho Task link](https://crm.zoho.eu/crm/org20100156718/tab/Tasks/{ID})\n{description}\n\nNote: Description is NOT synced...`. The migration script replaces this content with the footer `\n\n---\n[zoho:{ZOHO_ID}]` only (description body is discarded — description sync is out of scope). | Active | |
| SEED-4 | Run E2E test with at least one dummy task pair (create test task in Zoho, verify it appears in Todoist within 60s, edit it, verify propagation, complete it) before running the migration script against live tasks. | Active | User requirement: verify before touching live data |
| SEED-5 | Reconciliation cron: every 15 minutes, query Zoho for tasks modified in the last 20 minutes and Todoist incremental delta (via `sync_token`). For each changed task with a hash mismatch, enqueue a sync job. This serves as the primary catch-all for missed webhooks, dropped jobs, and bootstrap races. | Active | |
| SEED-6 | Orphan sweep (separate from reconciliation, runs hourly): for all `sync_state` rows, verify the Zoho task still exists and is assigned to me. For all `sync_state` rows, verify the Todoist task still exists. Apply two-cycle confirmation before orphan handling. | Active | |
| SEED-7 | `sync_token` persistence: store the Todoist Sync API `sync_token` in Postgres (table: `kv_store` or as a row in `sync_state` with a reserved key). On service restart, load stored token and resume incrementally. On token-not-found or corruption, fall back to full sync (`sync_token="*"`). | Active | |

---

## Observability (OBS)

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| OBS-1 | `GET /health` endpoint returns within 100ms using only DB/cached values (no live API calls). Response: `status` (ok/degraded/error), `last_sync` (timestamp + direction), `queue` (depth, in_progress, failed), `errors_24h`, `echoes_suppressed_24h`, `syncs_24h`, `active_tasks`, `reconciler.last_run`. HTTP 200 for ok/degraded, HTTP 503 for error. | Active | |
| OBS-2 | All sync events logged to `sync_events` table: `action` in `{sync, echo_suppressed, overwrite, orphan, error}`, `source` in `{zoho_webhook, todoist_webhook, reconciler, migration}`, `detail` JSONB for context. | Active | |
| OBS-3 | Daily Todoist task auto-created (via arq cron, midnight UTC): title `Sync summary: {date}`, content `{N} syncs, {M} errors, {P} echoes suppressed`. | Active | |
| OBS-4 | `sync_events` cleanup: delete rows older than 90 days. Run as part of the daily summary task. | Active | |
| OBS-5 | Structured logging to stdout (Railway captures it). Log level configurable via `LOG_LEVEL` env var (default `INFO`). Log WARN for: dropped duplicate jobs, orphan 404s (first cycle), Resend failures. Log ERROR for: API write failures (before arq retry), DB update failures. | Active | |

---

## Out of Scope (v1)

| What | Reason |
|------|--------|
| New Todoist tasks creating Zoho tasks | Creation is deliberately one-way; Zoho is source of truth for task creation |
| Syncing Zoho tasks assigned to others | Only my tasks are relevant |
| Description sync | User ran Make.com without it; descriptions are independent per system |
| Zoho Projects (separate from CRM Tasks) | Different module, different complexity; possible Phase 2 project |
| UI or admin dashboard | Background service only; `/health` is sufficient |
| Backfilling completed/historical tasks | v1 seeds only currently-open tasks |
| Recurring task recurrence mapping | Cross-system recurrence is complex enough to be its own project |
| Priority roundtrip for "Lowest" | Zoho "Lowest" maps to Todoist 1 (same as "Low"); round-trip returns "Low". Acceptable data loss, logged as known behaviour. |
| V2: Zoho task link + Deal title in Todoist content | Noted for future; user mentioned they had this in Make.com |

---

## Validated Constraints

These are hard constraints that must be encoded in unit tests before any sync code ships:

| Constraint | Test |
|------------|------|
| Priority mapping is NOT inverted | Round-trip test: Zoho "Highest" → Todoist `priority=4` → Zoho "Highest" |
| Due date is always date-only | `Due_Date="2026-05-01T00:00:00+05:30"` normalises to `"2026-05-01"` |
| Footer is stripped before hashing | Hash of task with footer == hash of same task without footer (same body) |
| Description is excluded from hash | Changing description does not change canonical hash |
| Labels are excluded from hash | Adding/removing a Todoist label does not trigger a sync write |

---

## Known Open Questions (resolve during Phase 1 implementation)

1. Exact `api_name` of `Todoist_Task_ID` custom field after startup fetch from Zoho settings endpoint
2. Actual Zoho `Status` picklist values for this org (any custom done-statuses beyond "Completed"?)
3. Raw `Due_Date` format from this Zoho org (date-only or datetime with timezone offset?)
4. Zoho webhook payload structure for task deletion vs. reassignment vs. edit
5. Whether Todoist Sync API supports server-side project filtering or must filter client-side
6. arq cron `"*/15 * * * *"` syntax support in the pinned arq version
7. Exact Zoho `criteria` parameter syntax for v6/v7 API `Modified_Time` filter

---

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-1 | Phase 1, Phase 5, Phase 6 | Pending |
| INFRA-2 | Phase 1 | Pending |
| INFRA-3 | Phase 1, Phase 5 | Pending |
| INFRA-4 | Phase 1 | Pending |
| INFRA-5 | Phase 1, Phase 8 | Pending |
| INFRA-6 | Phase 1, Phase 2 | Pending |
| INFRA-7 | Phase 1, Phase 2 | Pending |
| SYNC-1 | Phase 4 | Pending |
| SYNC-2 | Phase 1, Phase 4 | Pending |
| SYNC-3 | Phase 4 | Pending |
| SYNC-4 | Phase 2, Phase 6 | Pending |
| SYNC-5 | Phase 1, Phase 3, Phase 4 | Pending |
| SYNC-6 | Phase 4 | Pending |
| SYNC-7 | Phase 1 | Pending |
| SYNC-8 | Phase 3, Phase 4, Phase 6 | Pending |
| SYNC-9 | Phase 1, Phase 3 | Pending |
| SYNC-10 | Phase 5, Phase 7 | Pending |
| SYNC-11 | Phase 5 | Pending |
| LOOP-1 | Phase 1, Phase 5 | Pending |
| LOOP-2 | Phase 1 | Pending |
| LOOP-3 | Phase 5 | Pending |
| LOOP-4 | Phase 2, Phase 5 | Pending |
| LOOP-5 | Phase 5, Phase 6 | Pending |
| EDGE-1 | Phase 4 | Pending |
| EDGE-2 | Phase 4 | Pending |
| EDGE-3 | Phase 4 | Pending |
| EDGE-4 | Phase 4 | Pending |
| EDGE-5 | Phase 7 | Pending |
| EDGE-6 | Phase 4 | Pending |
| EDGE-7 | Phase 4, Phase 6 | Pending |
| EDGE-8 | Phase 6, Phase 7 | Pending |
| SEED-1 | Phase 8 | Pending |
| SEED-2 | Phase 8 | Pending |
| SEED-3 | Phase 8 | Pending |
| SEED-4 | Phase 8 | Pending |
| SEED-5 | Phase 7 | Pending |
| SEED-6 | Phase 7 | Pending |
| SEED-7 | Phase 3, Phase 7 | Pending |
| OBS-1 | Phase 8 | Pending |
| OBS-2 | Phase 5 | Pending |
| OBS-3 | Phase 8 | Pending |
| OBS-4 | Phase 8 | Pending |
| OBS-5 | Phase 1 | Pending |

---

*Last updated: 2026-04-23 — traceability table added after roadmap creation*
