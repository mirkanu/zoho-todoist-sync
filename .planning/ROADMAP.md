# Roadmap: zoho-todoist-sync

## Overview

Build a loop-safe, two-way sync service between Zoho CRM Tasks and Todoist, deployed as two Railway services (web + worker). The build order flows from correctness-critical foundations (normalisation, schema) through read-only API integrations, then write operations, then the job worker that ties them together, then webhook ingestion, then the reconciliation/orphan sweeps that make the system reliable at rest, and finally the operational layer (health, observability, migration). Each phase is independently testable before the next begins.

## Phases

- [ ] **Phase 1: Foundation** - Schema, config, canonical hash, payload normalisation, and correctness-critical unit tests
- [ ] **Phase 2: Zoho Read** - OAuth token refresh, fetch single task, fetch tasks modified since timestamp
- [ ] **Phase 3: Todoist Read** - API client, fetch single task, Sync API with sync_token, footer parser
- [ ] **Phase 4: Write Operations** - Todoist and Zoho create/update/complete/delete paths
- [ ] **Phase 5: arq Worker** - sync_task job function, dedup enqueue, per-task lock, retry config
- [ ] **Phase 6: Webhooks** - Zoho and Todoist webhook endpoints; HMAC verification; fast enqueue
- [ ] **Phase 7: Reconciliation & Orphan Detection** - 15-min sweep, hourly orphan sweep, Resend notifications
- [ ] **Phase 8: Observability & Migration** - /health endpoint, daily summary task, 90-day cleanup, migration script, E2E test

## Phase Details

**Plans**: 3 plans
  - [x] 01-01-PLAN.md — Project scaffold (pyproject, requirements, .python-version, .gitignore, .env.example) + fail-fast Settings config with unit tests
  - [x] 01-02-PLAN.md — Pure utility modules: normalise, canonical hash, priority mapping, structured logging + full unit test suite
  - [x] 01-03-PLAN.md — SQLAlchemy models (sync_state, sync_events, kv_store) + Alembic initial migration + FastAPI lifespan stub

### Phase 2: Zoho Read
**Goal**: The service can authenticate to Zoho and fetch task data, with proactive token refresh and graceful auth-failure handling — no writes to Zoho yet
**Depends on**: Phase 1
**Requirements**: INFRA-6, INFRA-7, SYNC-4, LOOP-4
**Success Criteria** (what must be TRUE):
  1. Zoho OAuth access token is refreshed proactively at 50 minutes (before the 60-minute expiry); a refresh token failure stops the service, logs ERROR, and sends an alert (not silently retried)
  2. On startup, the actual `api_name` for the `Todoist Task ID` custom field is read from `GET /crm/v6/settings/fields?module=Tasks` and cached; Zoho terminal statuses are read from the same endpoint and compared against `ZOHO_TERMINAL_STATUSES`
  3. `fetch_zoho_task(zoho_task_id)` returns a normalised task dict (all fields normalised per LOOP-2 rules) or raises a typed exception for 404, 401, and rate-limit (429) responses
  4. `fetch_zoho_tasks_modified_since(timestamp)` returns paginated results filtered by both `Modified_Time` and `Owner.id`; a `Modified_Time` filter is always present (never a full scan)
**Plans**: 2 plans
  - [ ] 02-01-PLAN.md — ZohoClient + typed exceptions + get_task + get_fields_metadata + fetch_tasks_modified_since + zoho_record_to_normalised adapter (SYNC-4, INFRA-7)
  - [ ] 02-02-PLAN.md — token_manager (refresh_access_token + proactive_refresh_loop + kv_store persistence) + FastAPI lifespan wiring (load/refresh token, resolve field metadata, start refresh task) (INFRA-6, INFRA-7, LOOP-4)

### Phase 3: Todoist Read
**Goal**: The service can authenticate to Todoist, fetch tasks, perform incremental Sync API polls, and extract the Zoho task ID footer from any description — no writes to Todoist yet
**Depends on**: Phase 2
**Requirements**: SYNC-5, SYNC-8, SYNC-9, SEED-7
**Success Criteria** (what must be TRUE):
  1. `fetch_todoist_task(todoist_task_id)` returns a normalised task dict or raises a typed exception; auth failures stop sync and alert rather than silently retry
  2. On startup, Todoist Sync API is called with `sync_token="*"` for a full snapshot; the returned `sync_token` is persisted to Postgres (`kv_store`); on restart, the stored token is loaded and incremental sync resumes
  3. `extract_zoho_id(description)` correctly parses `[zoho:(\d+)]` from any position in the description, returns `None` for tasks without the footer, and is verified by unit tests covering missing footer, footer mid-text, and footer after user edits
  4. `item:added` events for tasks without a `[zoho:ID]` footer are logged and discarded; tasks without the footer that arrive via the Sync API delta are ignored
**Plans**: TBD

### Phase 4: Write Operations
**Goal**: The service can create, update, complete, and delete tasks in both Todoist and Zoho with idempotent, normalised payloads
**Depends on**: Phase 3
**Requirements**: SYNC-1, SYNC-2, SYNC-3, SYNC-6, SYNC-8, EDGE-1, EDGE-2, EDGE-3, EDGE-4, EDGE-6, EDGE-7
**Success Criteria** (what must be TRUE):
  1. `create_todoist_task(zoho_task)` creates a Todoist task in the target project with the correct `[zoho:ID]` footer appended, `due_date` as date-only string (never `due_datetime`), and the correct Todoist priority integer (Highest → 4, High → 3, Normal → 2, Low/Lowest/unset → 1)
  2. `update_todoist_task` and `update_zoho_task` apply only the synced fields (title, due date, priority, status); `null` due date clears the field in the target system; Todoist labels are never touched
  3. `complete_todoist_task` and `complete_zoho_task` close the task in the respective system; `ZOHO_TERMINAL_STATUSES` is used (not a hardcoded `"Completed"` string)
  4. `delete_zoho_task` is called when a Todoist task is deleted; `delete_todoist_task` is called when a Zoho task is reassigned away; both send a Resend email notification to `manuelkuhs@gmail.com`; Resend failure is logged but does not roll back the deletion
  5. All write functions are idempotent: calling them twice with the same data produces the same result and does not duplicate records or emails
**Plans**: TBD

### Phase 5: arq Worker
**Goal**: The `sync_task` job ties together fetch, hash check, write, and DB update into a single reliable unit — with deduplication, per-task locking, and correct retry behaviour
**Depends on**: Phase 4
**Requirements**: SYNC-10, SYNC-11, LOOP-1, LOOP-3, LOOP-4, LOOP-5, INFRA-1, INFRA-3
**Success Criteria** (what must be TRUE):
  1. `sync_task(zoho_task_id)` follows the full pipeline: fetch live state from both APIs → compute canonical hashes → `SELECT FOR UPDATE` on `sync_state` row → if hashes match, log `echo_suppressed` and return; if not, write to target → update `sync_state.last_hash` → log `action='sync'` in `sync_events`
  2. Enqueuing `sync_task` with `_job_id=f"sync:{zoho_task_id}"` deduplicates concurrent webhooks for the same task; a `None` return from `enqueue_job` logs WARN; a per-task Redis `SETNX` lock (30s TTL) serialises any two jobs that slip through dedup
  3. Zoho-webhook-triggered jobs defer by 2 seconds (configurable via `ZOHO_JOB_DEFER_SECS`) before the Zoho API fetch, reducing the stale-read race window
  4. arq retry config: max 3 retries, backoff 5s/15s/60s; `job_timeout=60`; `keep_result=300`; API write failures raise and trigger retry; DB update failures raise and trigger retry (double-write is idempotent)
  5. When this service creates a new Todoist task, the resulting `item:added` webhook is identified as sync-managed (footer present) and suppressed without triggering a reverse sync
**Plans**: TBD

### Phase 6: Webhooks
**Goal**: FastAPI webhook endpoints for both Zoho and Todoist are live, validate payloads, and enqueue jobs within milliseconds — no sync logic lives in the handlers
**Depends on**: Phase 5
**Requirements**: SYNC-4, SYNC-8, INFRA-1, INFRA-4, LOOP-5, EDGE-7, EDGE-8
**Success Criteria** (what must be TRUE):
  1. `POST /webhooks/zoho` validates the payload contains `module` and `ids`, extracts the task ID, enqueues `sync_task` with a 2-second defer, and returns HTTP 200 within 200ms; any validation failure returns HTTP 400
  2. `POST /webhooks/todoist` verifies the HMAC-SHA256 signature in `X-Todoist-Hmac-SHA256` against the raw request body using `TODOIST_CLIENT_SECRET`; signature mismatch returns HTTP 401 immediately without processing
  3. Todoist `item:added`, `item:updated`, `item:completed`, `item:uncompleted`, and `item:deleted` events each reach the correct handler branch; `item:added` without a footer is discarded (logged); `item:deleted` enqueues the delete propagation path
  4. Both endpoints return HTTP 200 before any database or API I/O; the only synchronous operations are payload parsing and HMAC verification
**Plans**: TBD

### Phase 7: Reconciliation & Orphan Detection
**Goal**: Missed webhooks, dropped jobs, and orphaned task pairs are detected and resolved automatically without human intervention
**Depends on**: Phase 6
**Requirements**: SEED-5, SEED-6, SEED-7, LOOP-3, EDGE-1, EDGE-2, EDGE-5, EDGE-6, EDGE-8, SYNC-10
**Success Criteria** (what must be TRUE):
  1. Reconciliation cron runs every 15 minutes: fetches Zoho tasks modified in the last 20 minutes (with assignee filter) and the Todoist incremental delta (via `sync_token`); for each task with a hash mismatch, enqueues a `sync_task` job with dedup; the `sync_token` is updated in Postgres after each successful poll
  2. An edit made in Zoho while the webhook receiver was down (simulated) is picked up and synced within 20 minutes via the reconciliation sweep alone
  3. Orphan sweep runs hourly: for each `sync_state` row, verifies the Zoho task exists and is assigned to me, and the Todoist task exists; a single 404 is logged as WARN and noted in `sync_state.orphan_check_count`; a second consecutive 404 triggers orphan handling (delete from the live system, remove `sync_state` row, log `action='orphan'`, send Resend email)
  4. Reconciler last-run timestamp is updated in `kv_store` after each sweep; the `/health` endpoint reflects `reconciler.last_run` and flags `degraded` if it is stale beyond 25 minutes
**Plans**: TBD

### Phase 8: Observability & Migration
**Goal**: The service is fully observable, the 90-day audit log is self-maintaining, and all existing Make.com task pairs are linked into the new sync system without data loss
**Depends on**: Phase 7
**Requirements**: OBS-1, OBS-2, OBS-3, OBS-4, SEED-1, SEED-2, SEED-3, SEED-4, INFRA-5
**Success Criteria** (what must be TRUE):
  1. `GET /health` returns within 100ms using only DB/cached values; response includes `status` (ok/degraded/error), `last_sync`, `queue` depth/in_progress/failed, `errors_24h`, `echoes_suppressed_24h`, `syncs_24h`, `active_tasks`, and `reconciler.last_run`; returns HTTP 200 for ok/degraded, HTTP 503 for error
  2. A daily arq cron task (midnight UTC) creates a Todoist task titled `Sync summary: {date}` containing sync/error/echo counts, then deletes `sync_events` rows older than 90 days
  3. An E2E test with a dummy task pair completes successfully: create test task in Zoho → verify it appears in Todoist within 60s → edit title/due date/priority → verify propagation → complete task → verify completion propagated → verify no infinite loop in `sync_events`
  4. The migration script runs without error against live data: all existing `sync_state`-less task pairs (identified via `Todoist_Task_ID` custom field) are linked with correct `last_hash` values and Todoist descriptions updated to the `[zoho:ID]` footer format; no duplicate Todoist tasks are created; the Make.com preamble is replaced (not appended to)
  5. After migration, the live system processes at least 10 real sync events (from normal Zoho task usage) with zero infinite loops observed in `sync_events`
**Plans**: TBD

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 0/3 | Not started | - |
| 2. Zoho Read | 0/TBD | Not started | - |
| 3. Todoist Read | 0/TBD | Not started | - |
| 4. Write Operations | 0/TBD | Not started | - |
| 5. arq Worker | 0/TBD | Not started | - |
| 6. Webhooks | 0/TBD | Not started | - |
| 7. Reconciliation & Orphan Detection | 0/TBD | Not started | - |
| 8. Observability & Migration | 0/TBD | Not started | - |
