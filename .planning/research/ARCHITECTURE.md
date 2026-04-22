# Architecture Research

**Project:** zoho-todoist-sync
**Researched:** 2026-04-22
**Confidence:** MEDIUM (web tools unavailable; based on training data through Aug 2025 + project context)

---

## Loop Prevention Validation

### Is the canonical hash pattern standard?

The canonical hash approach is the correct pattern for two-way webhook sync. It is the production pattern used by systems like Zapier, Make.com's internal architecture, Merge.dev, and integration middleware generally. The core insight — normalise the payload identically regardless of which system it came from, hash it, store it, skip the echo if hashes match — is sound and solves the problem at the data layer rather than the timing layer (timing-layer approaches like "lock for 5 seconds after write" are fragile under retry and rate-limit conditions).

**Confidence: HIGH** — this is a well-established pattern; the PROJECT.md's framing of it is accurate.

### Known edge cases where the hash fails

**Edge case 1: Systems that mutate on write**
This is the most dangerous failure mode. When you write task data to Zoho or Todoist, the system may echo back the webhook with fields you did not set — for example, Zoho may normalise a due date string format, populate a `Last_Modified_Time` field, auto-assign an `Owner` if none was set, or silently add a default priority. If any of these mutated fields are included in the canonical hash, the incoming webhook will compute a *different* hash than the one stored, and the echo will not be suppressed.

**Mitigation:** The canonical hash must be computed only from the explicitly synced fields: `{title, due_date, description_body_only, priority, status}`. Exclude ALL metadata: modification timestamps, owner IDs, API-generated fields, system tags. Define the canonical payload struct explicitly in code and never hash anything outside it. This is not optional — it is the difference between the pattern working and not working.

**Edge case 2: Todoist description footer injection**
The footer `\n\n---\n[zoho:1234567890]` lives in the Todoist task description. If the canonical hash includes the raw Todoist description (with footer), and Zoho's description field does not include equivalent metadata, the hashes will never match for any Zoho-originated update. The hash must be computed from the *body only* — strip the footer before hashing. The PROJECT.md implies this but it must be made explicit in the hash function signature.

**Edge case 3: Due date normalisation drift**
Zoho may return `2026-05-01T00:00:00+05:30` for a date-only field. If the hash runs on the raw API string rather than the normalised `2026-05-01`, the echo detection breaks for timezone-shifted servers. The normalisation step (strip to `YYYY-MM-DD`) must happen *before* hashing, not after. This is already identified in PROJECT.md as a fix for the time-chip regression — confirm it applies equally to the hash function.

**Edge case 4: Priority mapping asymmetry**
Zoho priorities (High/Medium/Low/None) map to Todoist priorities (p1/p2/p3/p4). If this mapping is not perfectly invertible — e.g., if Zoho "None" maps to Todoist p4 but Todoist p4 maps back to "None" — the hash will be stable. But if there is a mismatch (e.g., two Zoho levels both map to p3), an edit originating in Todoist will hash differently than it did going in. Document the priority mapping table and verify it is a bijection for the fields you sync.

**Edge case 5: Race condition on first write**
When a Zoho task is first synced to Todoist, the sequence is:
1. Write to Todoist API
2. Store hash in `sync_state`

Between steps 1 and 2, Todoist fires a webhook. The hash is not yet in `sync_state`, so the webhook will not be suppressed. The worker processes it, finds no hash mismatch, and attempts to write back to Zoho — a no-op write that triggers another Zoho webhook, which finds no hash mismatch, and so on. This is the bootstrap race.

**Mitigation options (pick one):**
- Option A: Write a provisional hash to `sync_state` *before* the API write (optimistic lock), update it after. If the API write fails, delete the provisional record. This breaks the race window at the cost of a bit more DB complexity.
- Option B: Use the arq `job_id` deduplication (described below) to ensure that between the Todoist write completing and the DB update, any incoming webhook for the same task is queued but not yet processed. The hash will be stored by the time the queued job runs. This works if the DB update latency is less than the arq minimum retry delay — which it will be for local Postgres.
- Option C: After writing to Todoist, store the hash immediately (even before confirming Todoist success), and if the Todoist write fails, clean up. This is essentially the write-atomicity pattern discussed below.

Option B is the path of least resistance given arq is already in the stack. Option A is more explicit and does not depend on timing.

**Edge case 6: Hash collision**
SHA-256 collision probability is astronomically low for this payload size. Not a real concern. Using MD5 would be fine for this use case too, but SHA-256 is conventional for new code.

### How other systems handle it

- **Merge.dev / Finch / other integration middleware:** Use combination of origin-tagging ("write_source" field stored in DB) + hash comparison. Origin-tagging alone (only skip if you were the writer) is less reliable under concurrent writes; hash is the ground truth.
- **GitHub webhooks → third-party sync:** Store `X-GitHub-Delivery` UUID and deduplicate on that. Not applicable here since Zoho does not send a stable delivery ID with sufficient granularity.
- **Salesforce ↔ HubSpot sync vendors:** Use a combination of hash + source-of-truth field + version counter. Version counters add complexity that is not needed for a two-node sync.

**Verdict:** The canonical hash approach as designed is correct. The implementation must carefully define the canonical payload struct (no metadata, no timestamps, normalised values) and apply normalisation before hashing.

---

## Job Deduplication Pattern

### arq `job_id` parameter

arq supports job deduplication natively via the `job_id` parameter on `ArqRedis.enqueue_job()`. When you enqueue with an explicit `job_id`, arq uses Redis `SET NX` (set-if-not-exists) to write the job metadata. If a job with that `job_id` already exists in the queue (status: `queued` or `in_progress`), the second enqueue call returns `None` instead of a `Job` object — the job is silently dropped.

**Confidence: HIGH** — this is documented arq behavior, stable since arq 0.22+.

```python
job = await redis.enqueue_job(
    "sync_task",
    zoho_task_id,
    _job_id=f"sync:{zoho_task_id}",  # deduplication key
)
# job is None if a job with this ID already exists
```

**Key: use `zoho_task_id` as the dedup key.** Both Zoho webhooks and Todoist webhooks (which carry the embedded zoho_task_id from the footer) should use the same key format: `sync:{zoho_task_id}`.

### What happens when a duplicate arrives while a job is running

When the existing job is in `in_progress` state, arq's NX check still blocks the second enqueue — `job` is `None`. This means that if a webhook arrives for task X *while* the sync worker is actively processing task X, the new webhook is **dropped entirely**.

**This is a correctness risk.** The scenario:
1. User edits task X in Todoist (webhook arrives, job enqueued)
2. Worker starts processing, writes to Zoho, is about to update `sync_state`
3. User edits task X again in Todoist (webhook arrives, job is `in_progress`, new job dropped)
4. Worker finishes, stores hash of edit #1
5. Edit #2 is now lost — it will never be synced

**Mitigation:** Use a two-phase job ID:
- Phase 1 (dequeue): use `_job_id=f"sync:{zoho_task_id}"` — prevents pile-up
- Phase 2 (within the job): at end of execution, check if the hash stored matches the hash that was present when the job started. If not (i.e., something changed the `sync_state` during execution), re-enqueue a new job for the same task immediately.

An alternative is to not use arq deduplication at all and instead use a Redis `SET NX` distributed lock within the job, with exponential backoff retry. But this is more complex than needed for v1.

**Simpler v1 approach:** Accept the dropped-job edge case for now. It requires two rapid sequential edits with precise timing to manifest, and the reconciliation sweep will catch it within 15 minutes. Log `job is None` cases as `WARN` so they are visible.

### Job TTL

arq jobs have a default `keep_result` of 1 hour. For a sync service, set `keep_result=300` (5 min) — results are not queried, so there is no reason to retain them. Set `job_timeout=60` to prevent a hung Zoho/Todoist API call from holding a job slot indefinitely.

---

## Reconciliation Design

### Goal

The 15-minute sweep catches: failed webhooks, dropped jobs, bootstrap races, partial failures, and drift from rate-limit retries.

### Querying Zoho for recent changes

Use the CRM Tasks API with a `Modified_Time` criteria filter:

```
GET /crm/v7/Tasks?criteria=(Modified_Time:greater_than:{timestamp})&fields=id,Subject,Status,Due_Date,Priority,Description,Assigned_To&per_page=200
```

The timestamp should be `now - 20 minutes` (5-minute overlap beyond the 15-minute sweep period provides a safety buffer against clock skew and delayed webhook delivery).

Zoho API v7 supports `criteria` as a query parameter with comparison operators. Pagination via `page` parameter (default 200 per page). For a personal task list, hitting the second page is unlikely but must be handled.

**Rate limit concern:** The Zoho free tier is approximately 5000 API credits/day. A GET costs 1 credit. A 15-minute sweep costs roughly 100 API calls/day (96 sweeps/day + task fetches). This is well within limits *if* the sweep is efficient and does not degrade into full scans.

**Filter by assignee:** Add `Assigned_To.id:equals:{my_user_id}` to the criteria to avoid pulling other people's tasks. This requires knowing your Zoho CRM user ID — store it as an env var (`ZOHO_USER_ID`).

### Querying Todoist for recent changes

Todoist Sync API v9 uses `sync_token` for incremental updates. The flow:

1. On service startup (or after a restart), call `POST /sync` with `sync_token=*` to get a full snapshot. Store the returned `sync_token` in Postgres or Redis.
2. In the reconciliation sweep, call `POST /sync` with the stored `sync_token`. Todoist returns only items changed since that token was issued. Store the new `sync_token`.
3. Filter the returned `items` to those in the target project ID.

**Important:** The `sync_token` is per-session, not time-based. It cannot be used to query "tasks modified in the last 20 minutes" — it returns everything changed since the last sync call. This means the reconciler must call Todoist sync frequently (every 15 minutes) to keep the token fresh and the delta small.

**Do not use Todoist REST API v2 for the reconciler.** The REST API has no time-range filtering or incremental sync. The Sync API v9 is the right tool.

**Rate limits:** Todoist Sync API allows 450 requests per 15 minutes per user. One reconciliation sweep uses at most 2-3 Sync API calls. No concern.

### Reconciler algorithm (step by step)

```
1. Fetch Zoho tasks modified in last 20 min (with assignee filter)
2. Fetch Todoist incremental delta (via sync_token)
3. For each Zoho task in step 1:
   a. Look up sync_state by zoho_task_id
   b. Compute canonical hash of Zoho task
   c. If hash != sync_state.last_hash: enqueue sync job (with job_id dedup)
4. For each Todoist item in step 2 (that has a [zoho:ID] footer):
   a. Extract zoho_task_id from footer
   b. Look up sync_state by zoho_task_id
   c. Compute canonical hash of Todoist item
   d. If hash != sync_state.last_hash: enqueue sync job (with job_id dedup)
5. Orphan check: for all sync_state rows where zoho_last_seen > 20 min ago,
   verify task still exists in Zoho (with assignee filter)
```

The orphan check in step 5 is deferred to a separate scheduled job to avoid the sweep becoming too expensive per run.

### Handling reconciler/webhook worker conflicts (same task, concurrent writes)

The canonical hash check is the arbiter. When both the reconciler and a webhook worker detect a change and enqueue a job for the same `zoho_task_id`:

- arq's `job_id` dedup means only one job runs at a time for a given task key.
- The second job is dropped (or if the first job has finished, a second job starts after).
- Since both jobs fetch fresh data at execution time (not from the webhook payload), they will converge to the same state: the current live state in both systems.
- The hash stored by the first job's completion will cause the second job (if it runs) to no-op via the canonical hash check at the start of execution.

This is the correct behavior. The "fetch on dequeue, not on enqueue" design (already specified in PROJECT.md) is essential to this convergence.

**Failure scenario to guard against:** Reconciler enqueues a job. Webhook worker job starts and finishes *between* the reconciler's Zoho API fetch and its enqueue call. The reconciler then enqueues a job for stale data. Since the job fetches fresh data at execution time, it will fetch the current state, compute the hash, find it matches `sync_state.last_hash` (updated by the webhook worker), and no-op. No data loss.

---

## Schema Review

### Proposed tables (inferred from PROJECT.md)

Based on the project description, the schema likely has:

```sql
-- sync_state: one row per linked task pair
CREATE TABLE sync_state (
    zoho_task_id    TEXT PRIMARY KEY,
    todoist_task_id TEXT NOT NULL,
    last_hash       TEXT NOT NULL,         -- canonical hash of last synced state
    last_synced_at  TIMESTAMPTZ NOT NULL,
    zoho_last_seen  TIMESTAMPTZ,           -- last time Zoho confirmed task exists
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- sync_events: append-only audit log
CREATE TABLE sync_events (
    id              BIGSERIAL PRIMARY KEY,
    zoho_task_id    TEXT NOT NULL,
    action          TEXT NOT NULL,         -- 'sync', 'echo_suppressed', 'overwrite', 'orphan', 'error'
    source          TEXT NOT NULL,         -- 'zoho_webhook', 'todoist_webhook', 'reconciler'
    detail          JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### Issues and improvements

**Issue 1: No index on `sync_events.zoho_task_id`**
The health endpoint and daily summary query will filter `sync_events` by time range and count by action type. Without an index on `(created_at, action)`, these queries do a full scan of the events table. Add:

```sql
CREATE INDEX idx_sync_events_created_at ON sync_events (created_at DESC);
CREATE INDEX idx_sync_events_zoho_task_id ON sync_events (zoho_task_id, created_at DESC);
```

**Issue 2: No index on `sync_state.todoist_task_id`**
The Todoist webhook arrives with a Todoist task ID. The worker needs to look up `sync_state` by `todoist_task_id`. Without an index, this is a full table scan. For tens of tasks it is fast enough, but add the index from day one:

```sql
CREATE INDEX idx_sync_state_todoist_task_id ON sync_state (todoist_task_id);
```

**Issue 3: `BIGSERIAL` for `sync_events.id`**
`BIGSERIAL` is correct here. `SERIAL` (32-bit) could overflow over years of high-volume sync events, but `BIGSERIAL` (64-bit) will never overflow in practice. For an append-only audit log, `BIGSERIAL` is the right choice. No change needed.

**Issue 4: Missing `sync_state` locking primitive**
The schema as described has no row-level locking to prevent two workers from writing to the same `sync_state` row simultaneously. Use `SELECT ... FOR UPDATE` in the worker transaction that reads `sync_state` and prepares to write to the API. This serialises concurrent access to the same row. Example:

```sql
BEGIN;
SELECT last_hash FROM sync_state WHERE zoho_task_id = $1 FOR UPDATE;
-- if hash matches incoming, ROLLBACK (no-op)
-- if hash differs, proceed with API write, then UPDATE sync_state, COMMIT
```

Without `FOR UPDATE`, two workers can both read the same `last_hash`, both decide the hash differs, and both perform the API write — resulting in two identical writes to the target system (harmless but wasteful, and one of them triggers a suppressed echo on the next round).

**Issue 5: `sync_state.zoho_last_seen` null handling**
If `zoho_last_seen` is NULL (task never checked after creation), the orphan detection query must handle NULLs correctly. Use `COALESCE(zoho_last_seen, created_at)` in the orphan query.

### TTL/cleanup strategy for `sync_events`

`sync_events` is an append-only log. For a personal sync service with ~50 active tasks and maybe 200 sync events/day, the table will have 73,000 rows/year — trivial for Postgres. However, establish cleanup from day one to avoid surprises:

**Recommended strategy:** Retain events for 90 days. Run a cleanup as part of the daily summary task (already planned):

```sql
DELETE FROM sync_events WHERE created_at < now() - interval '90 days';
```

This is a cheap delete since `created_at` is indexed. Do not use a separate cron — attach it to the daily summary task so there is one less scheduled thing to manage.

**Do not use Postgres table partitioning or TimescaleDB** for this volume. It is unnecessary complexity.

---

## Write Atomicity Pattern

### The fundamental problem

There is no distributed transaction spanning an HTTP API call and a Postgres update. The failure modes are:

| Scenario | What happens |
|----------|-------------|
| API write succeeds, DB update succeeds | Normal |
| API write fails | Retry the whole job; idempotent because hash check will re-trigger |
| API write succeeds, DB update fails | **Dangerous: state is now inconsistent** |
| DB update succeeds, API write fails | Impossible in the proposed flow (API write comes first) |

### Safe pattern for "API write succeeds, DB update fails"

**Step 1:** Before the API write, store a provisional hash in Redis (not Postgres) with a 60-second TTL:

```python
await redis.set(f"pending_hash:{zoho_task_id}", new_hash, ex=60)
```

**Step 2:** Perform the API write.

**Step 3:** Update `sync_state` in Postgres. If this fails, the provisional hash in Redis will still suppress any incoming webhook for 60 seconds, buying time for the retry.

**Step 4:** On job retry (arq retries on exception), the job fetches fresh data from the API, computes the hash, and checks:
- Against `sync_state.last_hash` in Postgres (still stale — reflects pre-write state)
- Against the provisional Redis hash (reflects the write that succeeded)

If both checks are implemented, the retry will see the provisional hash matches the current live state and no-op cleanly. The DB update will then succeed on retry.

**Simpler alternative (acceptable for v1):** Skip the provisional Redis hash. If the DB update fails, arq retries the job. On retry, the job fetches current Zoho state and current Todoist state, finds they agree (because the API write succeeded), computes a hash, sees it differs from the stale `sync_state.last_hash`, and performs the API write again. The second write is idempotent (same data). The DB update succeeds. This is a redundant API call but causes no data corruption.

**Recommendation for v1:** Use the simpler alternative. The double-write scenario is rare (DB update failure after a successful API write requires a network partition or Postgres failure in a narrow window). Accept it. Log `WARN` when a DB update fails after an API write.

### Pattern for partial failure logging

```python
async def sync_task_worker(ctx, zoho_task_id: str):
    try:
        # 1. Fetch live state from both APIs
        # 2. Compute canonical hashes
        # 3. Hash check — no-op if match
        # 4. Write to target API
        # 5. Update sync_state (may fail)
        await update_sync_state(zoho_task_id, new_hash)
    except APIWriteError as e:
        await log_event(zoho_task_id, action='error', detail={'stage': 'api_write', 'error': str(e)})
        raise  # arq retries
    except DBUpdateError as e:
        await log_event(zoho_task_id, action='error', detail={'stage': 'db_update', 'error': str(e)})
        raise  # arq retries; double-write is idempotent
```

The `raise` is important — arq only retries on exception. Swallowing exceptions silently leaves the service in a bad state.

### `SELECT FOR UPDATE` in the hash check

Use `SELECT last_hash FROM sync_state WHERE zoho_task_id = $1 FOR UPDATE` at the start of the job's critical section. This prevents two concurrent jobs from both deciding to write. Since arq's `job_id` dedup should prevent two jobs from running concurrently for the same task, this is defence-in-depth. Under the v1 simplification (accept dropped second job), this lock will almost never contend.

---

## Orphan Detection Edge Cases

### Edge case 1: 404 due to permissions vs. deletion

Zoho returns HTTP 404 for both "task does not exist" and "you do not have permission to view this task". If a Zoho task was reassigned to someone else, the API may return 404 (task not visible to your OAuth token) rather than returning the task with a different `Assigned_To`.

**Mitigation:** Do not treat a single 404 as definitive. Use a two-step verification:
1. Fetch the specific task by ID. If 200: task exists, check `Assigned_To`.
2. If 404: wait one reconciliation cycle (15 min), fetch again. If 404 again: treat as deleted/inaccessible and proceed with orphan handling.

This adds at most 15 minutes of delay to orphan detection but eliminates false-positive deletions from transient 404s.

**Alternative:** Use the Zoho Tasks search/list API filtered by `Assigned_To:equals:{my_id}` instead of individual task fetches. If the task ID is not in the result set for two consecutive sweeps, it is genuinely absent. This is the more robust approach but costs more API credits.

### Edge case 2: Zoho API transient error vs. deletion

A 500, 503, or network timeout from Zoho during orphan verification must not be treated as deletion. Only 404 (with the two-cycle confirmation above) should trigger orphan handling. Log all non-200 responses with `WARN`.

### Edge case 3: Todoist task completed vs. deleted

Todoist distinguishes between `is_completed: true` (task completed) and the task being absent from the API response (deleted). The sync service handles completion separately (complete in Zoho). Deletion (task absent from Sync API results with `is_deleted: true` flag in the delta) is what triggers the "Todoist delete → Zoho delete" flow.

**Implementation note:** In the Todoist Sync API delta, deleted items appear with `is_deleted: true`. Do not infer deletion from a task not appearing in the delta — absence from the delta means "not changed since last sync_token", not "deleted". Only `is_deleted: true` in the delta means deletion.

### Edge case 4: Zoho task soft-deleted (moved to trash)

Zoho CRM has a recycle bin. A task moved to the recycle bin may return 404 or a special status via the API. If Zoho returns a `DELETED_ID` error code in its response body, treat that as deletion. If it returns 404, apply the two-cycle confirmation.

### Edge case 5: Todoist → Zoho delete triggers Zoho webhook

When the service deletes a Zoho task (in response to a Todoist deletion), Zoho fires a "task deleted" webhook. The webhook handler must recognise "task deleted" events and not treat them as sync triggers. Check the webhook payload type — Zoho workflow webhooks for deletions will have a different trigger type or a null `Subject`. Discard these gracefully and log them.

### Edge case 6: Email notification failure

If the Resend API call fails (after orphan detection or reassignment detection), the task deletion/handling has already occurred. Do not roll back the deletion because the email failed. Log the failure and let it be visible in the health endpoint's error count. Accept the "deleted but no email" outcome — attempting to retry Resend independently is more complexity than it is worth for v1.

---

## Health Endpoint Design

### What matters most for a sync service

The `/health` endpoint for a sync service serves two audiences: automated monitoring (Railway healthcheck, uptime tools) and human debugging when something seems wrong.

### Recommended response structure

```json
{
  "status": "ok",           // "ok" | "degraded" | "error"
  "timestamp": "2026-04-22T14:00:00Z",
  "last_sync": {
    "at": "2026-04-22T13:59:47Z",
    "zoho_task_id": "1234567890",
    "direction": "zoho→todoist"
  },
  "queue": {
    "depth": 0,             // arq jobs currently queued
    "in_progress": 0,       // arq jobs currently running
    "failed": 0             // arq jobs in failed state (need manual inspection)
  },
  "errors_24h": 12,         // count from sync_events where action='error' and created_at > now()-24h
  "echoes_suppressed_24h": 47,  // count from sync_events where action='echo_suppressed'
  "syncs_24h": 89,          // count from sync_events where action='sync'
  "active_tasks": 23,       // count of rows in sync_state
  "reconciler": {
    "last_run": "2026-04-22T13:50:00Z",
    "status": "ok"          // "ok" | "stale" (if last_run > 20 min ago)
  },
  "apis": {
    "zoho": "reachable",    // or "unreachable" (from last successful webhook/API call)
    "todoist": "reachable"
  }
}
```

### Status derivation

- `"ok"`: queue depth < 50, failed jobs = 0, reconciler ran in last 20 min, errors_24h < 10
- `"degraded"`: any of: queue depth 50-200, errors_24h 10-50, reconciler stale (15-25 min)
- `"error"`: queue depth > 200, failed jobs > 0, errors_24h > 50, reconciler stale > 30 min

These thresholds are conservative for a personal sync service — tune after observing real traffic.

### What NOT to include in /health

- Do not make live API calls to Zoho or Todoist in the health handler. Health endpoints must return within 100ms. Use cached/DB-derived values only.
- Do not include task titles or descriptions in the health response (PII/content exposure risk, even for a personal service).
- Do not include OAuth tokens or credentials in the response.

### Railway healthcheck configuration

Railway uses the health endpoint to decide whether to restart the container. The endpoint must:
- Return HTTP 200 for "ok" and "degraded" status (service is functional, just stressed)
- Return HTTP 503 for "error" status (service is broken, restart may help)

Restarting for "degraded" would cause thrashing — Railway would keep restarting a service that is functional but busy.

### Metrics to track beyond the brief

The brief specifies: last sync timestamp, 24h error count, queue depth. Add:
- `echoes_suppressed_24h` — proof the loop prevention is working; if this is 0, either nothing is syncing or the hash check is broken
- `active_tasks` — sanity check against Zoho assignee count
- `reconciler.last_run` — proves the sweep is running; catches cron silently dying
- `failed` jobs count — arq moves jobs to a failed set after max retries; these need human attention and must be surfaced

---

## Build Order Recommendation

Dependencies between components determine build order. Building in the wrong order means integration tests are impossible.

### Phase 1: Foundation (nothing can run without this)
1. **Postgres schema** (`sync_state`, `sync_events`, indexes)
2. **Redis connection** (arq requires it)
3. **Environment config** (all env vars: Zoho OAuth, Todoist token, DB URL, Redis URL, ZOHO_USER_ID, Resend key)
4. **Canonical hash function** (pure function, no dependencies; testable immediately)
5. **Payload normalisation** (due date → date-only, priority mapping, footer stripping)

The hash function and normalisation must be written and tested *before* any API integration. These are the correctness-critical components and they have no external dependencies.

### Phase 2: Zoho integration (read-only first)
1. **Zoho OAuth token refresh** (all other Zoho calls depend on a valid token)
2. **Fetch single Zoho task by ID** (used by webhook worker)
3. **Fetch Zoho tasks modified since timestamp** (used by reconciler)

Do not build Zoho write operations until Phase 4.

### Phase 3: Todoist integration (read-only first)
1. **Todoist API client setup** (token auth, simpler than Zoho OAuth)
2. **Fetch single Todoist task by ID** (used by webhook worker)
3. **Todoist Sync API with sync_token** (used by reconciler; initialise token on startup)
4. **Footer parser** (`[zoho:ID]` extraction regex)

### Phase 4: Write operations (both systems)
1. **Todoist create task** (with footer injection)
2. **Todoist update task** (title, due date, description, priority)
3. **Todoist complete/uncomplete task**
4. **Zoho update task** (title, due date, description, priority)
5. **Zoho complete task** (status field)
6. **Zoho delete task** (for Todoist-delete flow)

### Phase 5: arq worker
1. **`sync_task` job function** (the core worker: fetch → hash check → write → DB update)
2. **Job enqueue with `_job_id` dedup**
3. **Retry configuration** (max retries: 3, backoff: 5s/15s/60s)

### Phase 6: Webhook endpoints
1. **Zoho webhook endpoint** (validates payload, extracts task ID, enqueues job)
2. **Todoist webhook endpoint** (validates HMAC signature, extracts zoho_task_id from description, enqueues job)

Webhook endpoints are thin: validate → extract ID → enqueue → return 200. No business logic.

### Phase 7: Reconciliation sweep
1. **Scheduled arq job** (runs every 15 min via arq's cron syntax)
2. **Drift detection logic** (compare sweep results against sync_state hashes)
3. **Orphan detection** (two-cycle confirmation, Resend notification)

### Phase 8: Operational
1. **`/health` endpoint**
2. **Daily summary task** (Todoist task creation + sync_events cleanup)
3. **Seed script** (initial population of Zoho tasks → Todoist)

### Seed script is last for a reason

The seed script is a one-shot operation that reads all current Zoho tasks, creates them in Todoist, and populates `sync_state`. It depends on every component above it. Running it before the system is stable will create orphaned Todoist tasks and inconsistent `sync_state` rows that are hard to clean up. Build it last, test it against a small subset first.

---

## Confidence Assessment

| Area | Level | Reason |
|------|-------|--------|
| Canonical hash pattern validation | HIGH | Well-established integration middleware pattern; edge cases are from first-principles analysis |
| arq `job_id` deduplication behavior | HIGH | Stable arq API, behavior is unambiguous from source; knowledge cutoff Aug 2025 |
| Todoist Sync API sync_token | HIGH | Stable Todoist API, documented behavior |
| Zoho API `Modified_Time` filter | MEDIUM | Known Zoho API capability; specific parameter syntax may differ slightly between API versions |
| Schema recommendations | HIGH | Standard Postgres patterns |
| Write atomicity pattern | HIGH | Standard distributed systems pattern |
| arq retry/timeout behavior | HIGH | Documented arq behavior |
| Zoho 404 behavior (deletion vs. permissions) | MEDIUM | Inferred from Zoho API behavior patterns; should be validated against actual API responses during integration testing |

---

## Open Questions (resolve during implementation)

1. **Zoho webhook payload format for deletions:** Confirm what Zoho sends in the webhook payload when a task is deleted vs. reassigned vs. edited. The handler needs to branch on this.
2. **Zoho `criteria` parameter syntax for v7 API:** Validate the exact query string format for `Modified_Time:greater_than` — Zoho API documentation has changed between v2, v6, and v7.
3. **Todoist Sync API project filter:** Confirm whether the Sync API delta can be filtered by project server-side, or whether filtering must happen client-side after fetching the full delta.
4. **Zoho OAuth token storage:** The current PROJECT.md does not specify where the OAuth refresh token is stored. It should be in Postgres (not env var) since it rotates. Add a `oauth_tokens` table or store in `sync_state` metadata.
5. **arq cron syntax for 15-minute intervals:** Confirm `cron("*/15 * * * *")` is supported in the arq version being used — older arq versions had limited cron support and required a separate scheduler.
