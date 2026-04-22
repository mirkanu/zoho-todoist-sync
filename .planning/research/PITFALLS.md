# Pitfalls Research: Zoho CRM ↔ Todoist Sync

**Domain:** Two-way webhook-driven task sync service
**Researched:** 2026-04-22
**Confidence note:** WebSearch and WebFetch are unavailable in this environment. All findings are drawn from training data (cutoff August 2025) supplemented by reasoning from the PROJECT.md design. Confidence levels are assigned honestly. Claims marked LOW should be verified against official docs before Phase 1 implementation begins.

---

## Zoho API Pitfalls

### Pitfall Z1: Webhook Delivery Is Best-Effort, Not Guaranteed

**Description:** Zoho CRM workflow webhooks (called "Notifications" in the API, or "webhook actions" in workflow rules) are triggered asynchronously by the workflow engine. Delivery is best-effort. Zoho does not publish a formal SLA for webhook delivery, and there is no documented retry policy for failed deliveries.

**What goes wrong:** If the Railway endpoint is unavailable (deploy in progress, cold start, OOM restart) when Zoho fires, the webhook is silently dropped. Zoho does not queue and retry failed webhooks the way Stripe or GitHub do. A Zoho task can be modified and the change never arrives.

**Confidence:** MEDIUM — behavior is consistent with Zoho's documentation philosophy (minimal reliability guarantees on workflow webhooks) and widely reported in Zoho developer forums, but the exact retry count (zero vs. a small number of retries) is not definitively documented in public API docs as of my knowledge cutoff.

**Prevention:**
- The reconciliation poller (Modified_Time query every N minutes) is the correct backstop — it catches edits that webhook delivery missed. Never remove it to "simplify" the architecture.
- Keep Railway service always-warm: set Railway to not scale to zero, or use a health-check ping cron.
- Return HTTP 200 from the webhook endpoint as fast as possible (enqueue to Redis, return immediately). A slow response that times out may be treated as a failed delivery.

**Phase to address:** Phase 1 (poller is non-optional from day one, not a "Phase 2 optimization").

---

### Pitfall Z2: Race Condition Between Workflow Trigger and API Consistency

**Description:** Zoho workflow rules fire their webhook action at the moment the trigger condition is met, which is during or immediately after the database write. However, the Zoho Tasks API (GET endpoint) may serve reads from a replica or cache that hasn't yet been updated. This means: webhook arrives → your worker fetches the task → API returns the pre-edit version → worker computes hash of stale data → hash matches stored hash → edit is silently skipped.

**What goes wrong:** The PROJECT.md correctly notes that "the sync worker must fetch the full task from the Zoho API on dequeue." But if dequeue happens within ~1-2 seconds of webhook delivery, stale data is a real risk.

**Confidence:** MEDIUM — eventual consistency windows are a known pattern in Zoho's infrastructure (multiple community reports of "GET returns old data after workflow runs"). Not officially documented.

**Prevention:**
- Add a deliberate delay before the Zoho API fetch on webhook-triggered jobs. Enqueue the job with a `defer_by=2` seconds in arq. This is sufficient to let replication propagate in normal conditions.
- If the fetched data's `Modified_Time` is older than the webhook arrival time (store arrival timestamp), re-enqueue with a short delay (exponential backoff starting at 3s, up to 30s, max 3 retries).
- Never compare the fetched data's timestamp to "now" — compare it to the webhook arrival time stored in the job payload.

**Phase to address:** Phase 1 — the job payload must store webhook arrival time from the start.

---

### Pitfall Z3: Zoho OAuth Refresh Token Revocation

**Description:** The PROJECT.md notes that "refresh tokens don't expire but can be revoked." The common revocation triggers are:

1. **New token generation for same client:** Generating a new access/refresh token pair for a self-client invalidates all previously issued tokens for that client. This is the most common accidental revocation. If you re-run the OAuth flow during development to get a fresh token, the old one stored in the DB is immediately invalid.

2. **Password change:** If the Zoho account password is changed, all active sessions and OAuth tokens across all clients are revoked.

3. **2FA reset or security event:** Zoho revokes tokens on suspicious activity, forced logout from admin, or 2FA device reset.

4. **Explicit revocation via Zoho's Connected Apps UI:** Any admin can revoke the app's access from Zoho's account settings.

5. **Token idle timeout (less certain):** There are community reports that Zoho revokes self-client refresh tokens that have not been used for 12+ months. Not officially documented for self-client tokens. Confidence: LOW.

**Confidence:** HIGH for items 1-4 (documented or widely confirmed). LOW for item 5.

**Prevention:**
- Store only ONE refresh token. Never regenerate unless forced.
- Catch HTTP 401 responses with error code `INVALID_TOKEN` or `ACCESS_TOKEN_EXPIRED` separately. On access token expiry → refresh automatically. On refresh token invalid → stop syncing, log a critical error, send an alert email via Resend immediately (this is an operator emergency, not a retryable condition).
- The health endpoint must surface "Zoho auth status: OK / REVOKED" as a first-class field, not buried in error count.
- Document the "if you regenerate the token during debugging, update the DB" warning in the README.

**Phase to address:** Phase 1 — auth error handling must be complete before any webhook processing.

---

### Pitfall Z4: Zoho API Consistency — Modified_Time Is Not Always Updated

**Description:** Not all field changes update `Modified_Time` on the Zoho Tasks record. Known cases:

1. **Custom field updates via workflow:** When a Zoho workflow (not a user action) writes to a custom field, `Modified_Time` may not be updated. This is relevant because the sync service writes `Todoist_Task_ID` to a custom field. If that write triggers another workflow, the Modified_Time may or may not change.

2. **Computed/roll-up fields:** If any computed field is recalculated in the background, it may not bump Modified_Time.

3. **The `Todoist_Task_ID` custom field write itself:** When the sync service writes the Todoist task ID back to the Zoho task, this write MUST NOT trigger a webhook + sync cycle. If `Modified_Time` does update on custom field write and a workflow rule is watching `Modified_Time` changes, you'll get a spurious webhook. The canonical hash check is the defense here, but see Z2 — stale data could cause the hash check to incorrectly pass.

**Confidence:** MEDIUM — the behavior of Modified_Time on custom field writes via API is inconsistently documented. Training data includes community reports of this being an issue.

**Prevention:**
- Scope the Zoho workflow rule trigger as narrowly as possible: trigger only on changes to specific fields (Title, Due_Date, Description, Status, Priority, Owner), not on ANY field change. This prevents the `Todoist_Task_ID` write from triggering a webhook.
- If field-specific workflow triggers aren't available in your Zoho CRM edition, use the `Modified_By` field: if the API user (the sync service's OAuth user) is the `Modified_By`, skip processing immediately before computing the hash.
- The `Modified_By` guard is a cheap, reliable complement to the hash check.

**Phase to address:** Phase 1 — the workflow rule configuration must be correct before go-live.

---

### Pitfall Z5: Zoho Tasks "Closed" vs "Completed" and Other Terminal Statuses

**Description:** Zoho CRM Tasks have a `Status` field that is a picklist. The default values include:
- `Not Started`
- `In Progress`  
- `Waiting for input`
- `Deferred`
- `Completed`

However, Zoho allows administrators to customize this picklist. "Closed" is NOT a default Zoho CRM Task status — it's a Zoho Projects concept. If previous integrations (zzzBots, Make.com) referenced "Closed," they may have been working with a customized picklist or conflating CRM Tasks with Projects tasks.

**What goes wrong:** If the account has a custom status like "Done," "Closed," "Cancelled," or "Won't Do" added to the picklist, and the sync service only checks for `status == "Completed"`, tasks marked with those custom statuses will never be completed in Todoist.

**Confidence:** HIGH for the default picklist. MEDIUM for the risk of custom statuses being present in this account.

**Prevention:**
- On startup, fetch the Tasks module metadata via the Zoho Fields API (`/crm/v7/settings/fields?module=Tasks`) to get the current Status picklist values dynamically.
- Define "done" as a configurable set of status values, not a hardcoded string. Default to `["Completed"]` but allow configuration via env var `ZOHO_DONE_STATUSES=Completed,Done,Closed`.
- Log a warning on startup if the live picklist contains values not in the configured done-set.
- When syncing a task with an unrecognised status to Todoist, treat it as "not done" (safe default), not as "done."

**Phase to address:** Phase 1 for the config approach; startup validation in Phase 2.

---

### Pitfall Z6: Zoho Due Date Format Inconsistency

**Description:** The PROJECT.md correctly identifies this: Zoho may return due dates as `YYYY-MM-DDTHH:MM:SS+offset` even when the user set only a date. The offset depends on the Zoho account's timezone setting, not the user's local timezone, and it can shift the displayed date by one day when the offset crosses midnight.

**Additional detail:** The Zoho API due date field for Tasks is called `Due_Date`. It is documented as returning a date string, but in practice the format includes a time component when the task was created via certain methods (API vs. UI). The time component, if present, is typically `00:00:00` at the account timezone, which when rendered in UTC can appear as the previous day.

**What went wrong with Make.com:** Make.com likely passed the raw timestamp to Todoist's `due_datetime` parameter instead of extracting only the date. Todoist then rendered the time in the user's local timezone, which showed a different time-of-day. This is the "incorrect time-of-day" bug described in the PROJECT.md.

**Confidence:** HIGH — this is well-documented behavior and the PROJECT.md explicitly flags it.

**Prevention:**
- Always parse the due date with a date-aware parser and extract only `YYYY-MM-DD`. Never pass `due_datetime` to Todoist. Only ever pass `due_date` (string, `YYYY-MM-DD`) or `due_string` (natural language, use with caution).
- In the canonical hash, store the normalised date-only string. Never store the raw timestamp.
- Add a unit test: given `"2025-03-15T00:00:00+05:30"`, assert the normalised output is `"2025-03-15"` (not `"2025-03-14"`). Use `datetime.fromisoformat()` → `.date()` → `str()`, not string slicing.

**Phase to address:** Phase 1 — encode this in the normalisation function from day one.

---

## Todoist API Pitfalls

### Pitfall T1: Todoist Webhook Delivery — At-Least-Once, No Ordering

**Description:** Todoist webhooks are delivered at-least-once. There is no exactly-once guarantee. Ordering is not guaranteed — two events for the same task may arrive out of order.

**Retry behavior:** If the endpoint returns a non-2xx status, Todoist retries delivery with exponential backoff. The exact retry count and window are not publicly documented to a precise number, but the system will retry for a period measured in hours, not days. After exhausting retries, the event is dropped.

**Confidence:** MEDIUM — at-least-once is stated in Todoist's developer docs. Retry specifics (count, backoff schedule) are not precisely documented publicly.

**Prevention:**
- Return HTTP 200 immediately. Enqueue to Redis (arq), do not process inline.
- All sync handlers must be idempotent: processing the same webhook twice must produce the same result (canonical hash check handles this).
- The sync_state table provides the ground truth. If two webhooks arrive for the same task, the second will either be a no-op (hash matches) or a legitimate update (hash differs, LWW applies).
- Do not rely on Todoist webhook ordering for correctness. The reconciliation poller is the ordering backstop.

**Phase to address:** Phase 1.

---

### Pitfall T2: Todoist Task Description — Footer Survival

**Description:** The `[zoho:ID]` footer strategy relies on Todoist preserving the description content exactly. There are two risks:

1. **Todoist UI truncation:** The Todoist mobile app displays truncated descriptions in list views, but does not modify the stored content. The full content is always preserved in the API. This is safe.

2. **User deletion of footer:** A user who edits the description in Todoist may accidentally delete the `---\n[zoho:ID]` footer if they select-all and replace. This severs the link permanently.

3. **Markdown rendering:** Todoist renders `---` as a horizontal rule in its UI. The footer will visually appear as a line separator, which is aesthetically acceptable but may confuse users into deleting it.

4. **Todoist REST API vs Sync API description field name:** The REST API uses `description`; the Sync API uses `content` (for task name) and `description` (for body). Confirm the correct field name for each API call — using `content` when you mean `description` will silently set the title, not the body.

**Confidence:** HIGH for items 1-3 (well-established Todoist behavior). MEDIUM for item 4 (verify against current Todoist API docs during implementation).

**Prevention:**
- When syncing a Todoist update back to Zoho, always re-append the footer before writing if it's missing. The re-append must be idempotent (check for existing footer before appending).
- If the footer is missing when a Todoist webhook arrives, treat it as an orphaned task: log a warning, skip the sync, send an alert. Do not attempt to re-link without manual intervention (the Zoho task ID is gone).
- Document clearly: "Do not edit the footer line in Todoist task descriptions."
- Consider making the footer slightly more resistant: `<!-- zoho:ID -->` (HTML comment) would survive visual editing better, but Todoist may strip HTML comments. Verify before choosing. The `[zoho:ID]` format is likely safer.

**Phase to address:** Phase 1 for footer handling logic; consider a monitoring alert for orphaned tasks in Phase 2.

---

### Pitfall T3: Todoist Sync API — sync_token Invalidation

**Description:** The Todoist Sync API uses incremental sync via `sync_token`. If you pass an invalid or expired sync_token, the API returns an error (or, in some versions, silently performs a full sync and returns a new token). The token can become invalid if:

1. **Extended inactivity:** If the service is down for an extended period (days to weeks), the stored sync_token may be rejected as too old. Todoist does not document the exact TTL for sync tokens.

2. **Account-level reset:** If the Todoist user revokes the API token and generates a new one, the sync_token is implicitly invalidated.

3. **Sync API version mismatch:** If you upgrade the Sync API version (v8 → v9), old sync_tokens are not valid across versions.

**Confidence:** MEDIUM — sync_token invalidation on inactivity is reported in Todoist developer forums but not precisely documented. The recovery path (full sync with `sync_token="*"`) is documented.

**Prevention:**
- On startup always begin with a full sync (`sync_token="*"`) to establish a fresh baseline. Store the returned sync_token in Postgres (not Redis — Redis is volatile).
- Detect sync_token errors: if the Sync API returns an error response indicating invalid token, immediately fall back to `sync_token="*"` for a full sync, log the event, and continue.
- After a Railway service restart (Redis flush), the sync_token in Postgres survives. Always read sync_token from Postgres.
- Rate limit awareness: a full sync on startup counts against the 450 req/15 min limit. If the Todoist project has thousands of tasks, a full sync may consume significant quota. For this use case (single project, likely < 200 tasks), this is not a concern.

**Phase to address:** Phase 1 — startup sync initialization is foundational.

---

### Pitfall T4: Todoist API Token vs OAuth

**Description:** The `todoist-api-python` library supports both personal API tokens and OAuth. For a single-user personal service, the personal API token is simpler. However:

- Personal API tokens do not expire but are tied to the account. If the account's password is changed or security is reset, the personal token may be invalidated.
- The personal token is available in Todoist Settings → Integrations → Developer. It does not require an OAuth flow.

**Confidence:** HIGH.

**Prevention:** Store the personal API token as a Railway env var. Monitor for HTTP 401 responses from Todoist and surface them the same way as Zoho auth failures (stop syncing, send alert email, flag health endpoint).

**Phase to address:** Phase 1.

---

### Pitfall T5: Todoist Completing a Task vs Closing a Task

**Description:** In Todoist, completing a task (checking it off) and "closing" (archiving) a recurring task are different operations. For non-recurring tasks (which is what this sync handles), completing is straightforward. However:

- The Todoist webhook event for task completion is `item:completed`, not `item:updated`.
- If you only subscribe to `item:updated` webhooks, you will miss completions.
- The Sync API uses `items/complete` for the command, not `items/update`.

**Confidence:** HIGH — this is documented in Todoist's API reference.

**Prevention:**
- Subscribe to both `item:updated` and `item:completed` (and `item:uncompleted`) Todoist webhook events.
- Handle `item:completed` as a distinct event type that maps to closing the Zoho task.
- Handle `item:deleted` (for the Todoist delete → Zoho delete requirement) as another distinct event.

**Phase to address:** Phase 1 — webhook subscription setup.

---

## Sync Logic Pitfalls

### Pitfall S1: Canonical Hash Differences Due to Normalisation Gaps

**Description:** The hash-based loop prevention works only if the same logical state produces identical hash inputs regardless of which system it was read from. Common sources of divergence:

**1. Unicode normalisation (HIGH risk):** Zoho and Todoist may return the same string in different Unicode normalisation forms (NFC vs NFD). A character with a diacritic (e.g., "é") can be encoded as a single code point (NFC) or as a base letter + combining accent (NFD). These are visually identical but byte-different. `hashlib` will produce different hashes.

**Prevention:** Always run `unicodedata.normalize("NFC", text)` on all string fields before hashing.

**2. Whitespace in description (HIGH risk):** The Todoist description field may normalize trailing newlines or add/remove whitespace. The Zoho description field may return CRLF (`\r\n`) line endings from some API clients. After round-tripping Zoho description → Todoist → Zoho, the normalized value may differ from the original by a trailing newline.

**Prevention:** Strip trailing whitespace from all string fields before hashing (`text.strip()`). Normalize line endings to `\n` (`text.replace("\r\n", "\n").replace("\r", "\n")`).

**3. The `[zoho:ID]` footer in the hash (HIGH risk):** The Todoist description includes the `[zoho:ID]` footer; the Zoho description does not. If the description field is included in the hash naively, they will always differ. The hash must be computed over the "user-visible" description only — strip the footer before hashing.

**Prevention:** Define a `normalise_description(text: str) -> str` function that strips the footer (regex: `\n*---\n\[zoho:\d+\]\s*$`), then strips trailing whitespace, then normalises Unicode and line endings.

**4. None vs empty string (MEDIUM risk):** Zoho returns `null` for an unset description. Todoist returns `""` for an unset description. After the first sync, if the description was null in Zoho, Todoist stores `""`. On the next reconciliation, Zoho still returns `null`, Todoist returns `""`. The hash of `None` differs from the hash of `""`.

**Prevention:** Coerce `None` to `""` before hashing, for all nullable string fields.

**5. Date format (LOW risk given the design):** Already addressed by normalising to `YYYY-MM-DD`. But ensure the hash input is always the date string, never a `datetime` object, `date` object, or `None` when no due date is set (use a sentinel like `""` or `"none"`).

**Confidence:** HIGH for items 1-4 (these are textbook UTF-8/string normalisation issues). MEDIUM for item 5.

**Phase to address:** Phase 1 — write unit tests for each normalisation edge case before writing any sync logic.

---

### Pitfall S2: Priority Mapping Edge Cases

**Description:** Zoho CRM Task Priority is a picklist with default values: `High`, `Medium`, `Low`, `None` (or unset/null). Todoist priorities are integers: 4 (p1/urgent), 3 (p2/high), 2 (p3/medium), 1 (p4/no priority).

**Edge cases:**

1. **Zoho priority is null/unset:** The API may return `null`, `""`, or the string `"None"` for tasks without a priority set. All three must map to the same Todoist value (priority 1). The hash must normalise all three to the same representation.

2. **Custom priority values:** If the Zoho admin has added custom priority values to the picklist (e.g., "Critical," "Urgent"), the mapping will fail silently — the task will be assigned the default priority.

3. **Todoist priority 1 (no priority) vs Zoho "None":** After a round-trip, Zoho "None" → Todoist p4 (1) → Zoho "Low" (if that's the mapping for p4). This creates a priority upgrade on the first sync of an unset-priority task. The mapping must be: Todoist p4 maps to Zoho priority `None` or `Low` — choose one and be consistent. Recommended: unset in Zoho → p4 in Todoist → on write-back, set Zoho to `None` (not "Low").

**Confidence:** HIGH for the picklist defaults and the null handling issue. MEDIUM for the round-trip upgrade risk (depends on the specific mapping chosen).

**Prevention:**
- Define an explicit bidirectional mapping table in code, not ad-hoc if/elif chains.
- On startup, fetch the Zoho Priority picklist values from the Fields API. Log a warning if any live value is not in the mapping table.
- The canonical hash must normalise all "no priority" representations (null, `""`, `"None"`) to a single sentinel before hashing.

**Phase to address:** Phase 1.

---

### Pitfall S3: Completion Semantics — One-Way vs Two-Way

**Description:** The PROJECT.md requires: "Completing a task in either system completes it in the other." There are asymmetric edge cases:

1. **Completing in Todoist while Zoho is unreachable:** The Todoist webhook arrives, the Zoho API call fails, the task is marked complete in Todoist but not in Zoho. On next retry, the Todoist task is already complete so the webhook won't re-fire. The reconciliation poller must detect this: Todoist task completed, Zoho task open → close Zoho task.

2. **Re-opening a completed task in Zoho:** Zoho allows changing status from "Completed" back to "In Progress." The sync must handle this: completed Zoho task → open Todoist task (via `items/uncomplete` in the Sync API, or `is_completed: false` in the REST API). If the Todoist task has been archived (Todoist archive on completion is optional), uncompleting it may fail or require special handling.

3. **Todoist task deleted after being completed:** If a user completes a task in Todoist and then the automatic Todoist cleanup (completed task archiving) moves it out of the active tasks list, it may not be fetchable via the incremental sync. The sync_state table must track completion status separately from task existence.

**Confidence:** HIGH for item 1 (reliable class of API failure). MEDIUM for items 2-3 (Todoist's archive behavior is version-dependent).

**Prevention:**
- Store `completion_status` in sync_state. The reconciliation poller checks: if Todoist shows completed but Zoho shows open, push completion to Zoho.
- Handle `items/uncomplete` explicitly for re-open events from Zoho.
- Do not rely on Todoist task listability to infer non-completion. A completed+archived task is still queryable by ID.

**Phase to address:** Phase 1 for the basic completion flow; re-open and archive edge cases in Phase 2.

---

### Pitfall S4: LWW Conflict Resolution Gaps

**Description:** Last-write-wins is specified for simultaneous edits. "Last" is determined by the order events arrive at the service, which is non-deterministic. Two specific problems:

1. **Both systems fire webhooks simultaneously:** Zoho fires a webhook (user edits in Zoho). Todoist fires a webhook (user edits in Todoist at the same second). Both arrive within milliseconds. The second job to acquire the task's processing lock wins, overwriting the first. The losing edit is silently discarded.

2. **The overwritten edit may be re-applied by the reconciliation poller:** The poller sees the Zoho task's `Modified_Time` is after the stored hash timestamp, fetches it, and re-applies the Zoho version, overwriting the Todoist edit that "won" the LWW race. This creates a delayed overwrite loop that the hash check may not catch (because the hash stored after the Todoist win doesn't match what Zoho has).

**Confidence:** MEDIUM — the severity depends on how precisely the timing works. This is an edge case in practice (truly simultaneous edits), but a correctness hole.

**Prevention:**
- For v1, LWW is acceptable. Log all LWW events with both values in `sync_events` for forensic inspection.
- Use a per-task Redis lock (arq job dedup by task ID) to serialize processing. This doesn't solve the problem but makes it deterministic.
- Accept the known limitation: in the specific case of true simultaneous edits, one edit will be lost. This is documented and acceptable for a personal workflow tool.

**Phase to address:** Phase 1 for logging; accept the limitation explicitly.

---

### Pitfall S5: arq Job Deduplication Semantics

**Description:** arq supports job deduplication via `job_id`. If two webhooks arrive for the same Zoho task within a short window, the second enqueue with the same `job_id` will be a no-op if the first is still queued (not yet running). This is the desired behavior.

**But:** If the first job is already running when the second webhook arrives, arq will NOT deduplicate — it will enqueue a second job. Both jobs then run, potentially racing against each other.

**Confidence:** HIGH — this is documented arq behavior (`job_id` dedup only works for queued, not running jobs).

**Prevention:**
- Use a Redis `SETNX` lock keyed by task ID at the start of each job handler. If the lock is held, re-queue the job with a short delay (3-5 seconds) and return immediately.
- Release the lock after the job completes (use a `try/finally` block to ensure release on error).
- Set a short TTL on the lock (30 seconds) to prevent deadlock if the worker crashes mid-job.

**Phase to address:** Phase 1.

---

## Operational Pitfalls

### Pitfall O1: Railway Service Cold Starts

**Description:** Railway can restart a service with zero notice (deploys, OOM kills, platform maintenance). A cold start means:
- The arq worker reconnects to Redis. Any jobs that were mid-execution when the worker died are requeued automatically by arq after a job timeout (configurable, default is long).
- The FastAPI webhook receiver is unavailable for 1-10 seconds during the restart. Webhooks received during this window are dropped (see Z1 — Zoho may not retry).

**Confidence:** HIGH for the general pattern. MEDIUM for specific timing.

**Prevention:**
- Set `keep_alive: true` (or the Railway equivalent) on the service to minimize cold starts.
- The reconciliation poller will catch any missed edits within its polling interval.
- Set arq job timeout low enough that stale jobs are requeued quickly after a restart (e.g., 60 seconds), but high enough that a slow Zoho API call doesn't time out prematurely (Zoho API can be slow — set 30 seconds for the API call, 60 seconds for the job).

**Phase to address:** Phase 1 for configuration; monitor in production.

---

### Pitfall O2: Redis Connection Limits

**Description:** Railway's Redis instance has connection limits based on tier. arq and FastAPI both open connections to Redis. Under normal load this is not an issue, but:
- arq uses a connection pool internally.
- If the Python process forks or the connection pool is misconfigured, you can exhaust the limit.
- Railway's Redis free tier is limited (exact number varies by plan).

**Confidence:** MEDIUM.

**Prevention:**
- Use a single Redis connection URL shared by arq and any manual Redis operations.
- Set an explicit max connection pool size in the arq settings.
- Monitor Redis connection count in the health endpoint.

**Phase to address:** Phase 2 (operational tuning, not day-1 blocker).

---

### Pitfall O3: Postgres Connection Pool Exhaustion

**Description:** FastAPI + async SQLAlchemy (or asyncpg directly) can accumulate idle connections if not properly configured. Under low traffic this is invisible. Under brief traffic spikes (reconciliation poll + multiple simultaneous webhooks), the pool can saturate.

**Confidence:** MEDIUM.

**Prevention:**
- Set `pool_size=5, max_overflow=2` for this workload (single-user service, low concurrency).
- Use `asyncpg` or `psycopg3` with `async with conn:` context management to ensure connections are returned promptly.
- Use `pool_pre_ping=True` to detect stale connections after Railway maintenance windows.

**Phase to address:** Phase 1 (configure correctly from the start, not after observing failures).

---

### Pitfall O4: Zoho API Rate Limits on Free Tier

**Description:** Zoho CRM free tier API limits are:
- API calls per day: 5,000 (approximately — exact limit depends on user count and edition).
- Some operations count as multiple API calls.

The reconciliation poller is the main rate consumer. If it polls every 5 minutes and each poll fetches modified tasks (potentially 1-10 records), this is ~288 polls/day. Well within limits. However, if the poller is misconfigured to do a full module scan (all tasks, not just Modified_Time filtered), it can consume the daily limit in hours.

**Confidence:** HIGH for the risk. MEDIUM for the exact daily limit (Zoho's free tier limits change).

**Prevention:**
- Always use `Modified_Time > [last_poll_timestamp]` filter in the poller query. Never fetch all tasks.
- Track the last successful poll timestamp in Postgres (not Redis).
- Log every API call with its response's `x-ratelimit-remaining` header (if Zoho returns this). Alert when below a threshold.
- Implement a circuit breaker: if remaining daily quota drops below 500, suspend the poller and alert via email.

**Phase to address:** Phase 1 (the filter is non-optional from day one).

---

### Pitfall O5: Resend Email Failure During Critical Alerts

**Description:** The PROJECT.md requires email notifications for reassignment and deletion events via Resend. If Resend is unavailable or the API key is wrong, these notifications will be silently dropped unless the code handles this explicitly.

**Confidence:** HIGH.

**Prevention:**
- Wrap all Resend calls in try/except. On failure, log at ERROR level with the full intended notification content.
- Do NOT make the sync operation fail because the email failed. The email is a notification, not a prerequisite. Decouple: complete the sync action (delete Todoist task), then attempt email, then log result.
- Store "pending email notifications" in a separate Postgres table if reliability of email delivery is critical. For v1, best-effort logging is acceptable.

**Phase to address:** Phase 1.

---

## What Previous Implementations Got Wrong

### Make.com Failure Analysis

**The bug:** "Incorrectly applies time-of-day to Zoho due dates when syncing to Todoist."

**Root cause (high confidence):** Make.com's Zoho CRM module returns the due date field as an ISO 8601 timestamp (e.g., `2025-03-15T00:00:00+05:30`). Make.com's Todoist module has two fields for due dates: "Due Date" (date-only) and "Due Datetime" (timestamp). The Make.com scenario almost certainly mapped the Zoho timestamp to Todoist's "Due Datetime" field instead of "Due Date." This caused Todoist to store a datetime with the time component, which it then displayed in the user's local timezone — typically showing as the previous day or at midnight, depending on timezone offset.

**Why this is hard to catch in Make.com:** Make.com's visual field mapping doesn't show the underlying API field names clearly. The "Due Date" visual label maps to `due.date` in Todoist's API; "Due Datetime" maps to `due.datetime`. The bug looks correct in Make.com's UI ("I mapped the date field to the date field") but is wrong at the API level.

**The secondary failure (loop prevention):** Make.com's webhook-triggered scenarios fire when a task changes. If Todoist sync causes a Zoho update (writing the Todoist ID to a Zoho custom field, for example), that Zoho update fires another webhook, which fires another Make.com run. Make.com has no canonical hash — its loop prevention relies on "don't change Zoho if the value is the same," which fails if field comparison is unreliable (e.g., comparing a timestamp to a date string returns "different").

**How this design prevents it:**
1. The normalisation step always extracts `YYYY-MM-DD` from Zoho's timestamp before constructing the sync payload. The Todoist write always uses `due_date` (string), never `due_datetime`.
2. The canonical hash is computed after normalisation. The hash comparison correctly identifies echoes regardless of how the underlying field is formatted.
3. The `Modified_By` guard (if implemented) provides a cheap pre-hash filter for self-writes.

### zzzBots Failure Analysis

**The bug:** "Broke ~4 weeks ago; vendor unresponsive."

**Root cause (medium confidence):** zzzBots is a managed sync service, not a custom integration. Its failure mode is vendor-side — either the service stopped handling Zoho's or Todoist's API format changes, or the vendor's infrastructure went down. The user has no control over and no visibility into this failure.

**How this design prevents it:** This is a self-hosted integration. Infrastructure failures are under the operator's control. The Railway deployment is the operator's own account. The code is the operator's own codebase. No external vendor dependency for the sync logic itself.

---

## Priority Mitigations for Phase 1

The following must be fully addressed before writing any sync logic. Getting these wrong requires a rewrite:

**1. Normalisation function — implement and unit test first (S1)**
Write `normalise_task(source, data) -> NormalisedTask` before any other sync code. Unit test every edge case: None description, CRLF line endings, `[zoho:ID]` footer, date timestamps, null priority, diacritics. The canonical hash must be deterministic and tested against real API responses.

**2. Webhook endpoint returns 200 immediately (T1, Z1)**
The FastAPI endpoint must enqueue to Redis and return 200 within milliseconds. No synchronous API calls in the webhook handler. This prevents both Zoho and Todoist from thinking delivery failed.

**3. Zoho API fetch delay on webhook-triggered jobs (Z2)**
Add `defer_by=2` (seconds) to all Zoho-webhook-triggered arq jobs. The stale-read race condition will silently corrupt data without this. Add a Modified_Time staleness check as a fallback.

**4. Auth error handling as a first-class concern (Z3, T4)**
Before any sync logic, implement: HTTP 401 on Zoho access token → refresh. HTTP 401 on Zoho refresh token → stop, alert email, flag health endpoint. HTTP 401 on Todoist → stop, alert email, flag health endpoint. These are different code paths and must be explicitly handled.

**5. Per-task Redis lock (S5)**
Implement the `SETNX` lock pattern before any job handler processes tasks. Race conditions without this lock will cause data corruption that is hard to debug after the fact.

**6. Zoho workflow rule scope (Z4)**
Configure the Zoho workflow rule to trigger only on specific field changes (not ANY change, not when `Modified_By` is the sync user). This is infrastructure configuration, not code, but must be done before go-live.

**7. Postgres sync_token storage (T3)**
Store the Todoist sync_token in Postgres from day one. Never Redis-only. A Redis flush (possible on Railway plan changes or restarts) would force a full sync at an inconvenient time.

**8. Zoho Status picklist — configurable done-set (Z5)**
Make the "done" status set configurable via env var from day one. Hardcoding `"Completed"` will silently fail if the account has custom statuses.
