# API Capabilities Research

**Project:** zoho-todoist-sync
**Researched:** 2026-04-22
**Confidence note:** WebSearch and WebFetch were unavailable during this research session. All findings are from training data (cutoff August 2025) plus explicit statements in PROJECT.md. Confidence levels are assigned per finding. HIGH = well-documented, stable API behaviour corroborated by multiple training sources. MEDIUM = known but detail may drift across API versions. LOW = inferred or single-source — must be verified before coding.

---

## Zoho CRM Tasks API

### Webhook Payload

**Verdict: Notification-only, not full payload. Worker must fetch full task on dequeue.**

Confidence: HIGH (also explicitly stated in PROJECT.md as a confirmed constraint).

Zoho CRM workflow webhooks (configured under Setup → Automation → Workflow Rules → Webhook Action) send a POST to your endpoint with a thin notification body. The payload contains:

```json
{
  "module": "Tasks",
  "ids": ["1234567890123456789"]
}
```

- `module` is the CRM module name (for Activities/Tasks this is `"Tasks"`).
- `ids` is a list of record IDs that triggered the notification. In practice a single-record trigger always yields one element.
- No field values are included in the webhook body itself.

**Implication:** The sync worker enqueues a job keyed on `(module, record_id)`. The job handler fetches the full task from `GET /crm/v6/Tasks/{id}` before doing anything else. This is intentional per PROJECT.md and correct per the API design.

Deduplication note: arq job keying on `task_id` naturally collapses rapid-fire webhook calls for the same record into a single fetch — this is the correct behaviour and suppresses the "edit storm" echo race.

---

### Field Names & Types

Confidence: HIGH for field names; MEDIUM for exact Due_Date format behaviour.

Zoho CRM Tasks (part of the Activities module) use these canonical API field names:

| Concept | API Field Name | Notes |
|---------|----------------|-------|
| Title / Subject | `Subject` | String, required |
| Description / Notes | `Description` | String, multiline |
| Due date | `Due_Date` | See format note below |
| Priority | `Priority` | Picklist — see values below |
| Status | `Status` | Picklist — see values below |
| Record owner | `Owner` | Object: `{"id": "...", "name": "...", "email": "..."}` |
| Owner ID (legacy) | `SMOWNERID` | String ID — older field, still present |
| Record ID | `id` | String (large integer as string) |
| Created time | `Created_Time` | ISO 8601 datetime |
| Modified time | `Modified_Time` | ISO 8601 datetime |
| Activity type | `Activity_Type` | String, value "Task" for tasks |
| Completed | `Closed_Time` | Populated when task is closed; null otherwise |

**Due_Date format — IMPORTANT:**

Zoho API v6 returns `Due_Date` as a date string: `"YYYY-MM-DD"` for tasks that have no time component set in the CRM UI. However, the API has been observed (in prior integrations and Make.com) to return a full ISO 8601 datetime with timezone offset (`"YYYY-MM-DDTHH:MM:SS+05:30"`) in some configurations, especially when timezone settings on the Zoho org differ from UTC.

**The PROJECT.md decision is correct and essential:** always normalise `Due_Date` to `YYYY-MM-DD` using `str(date_value)[:10]` or a date parser, never pass a time component to Todoist. This prevents the "time chip regression" described in PROJECT.md.

When writing Due_Date back to Zoho, send `"YYYY-MM-DD"` string only.

**Priority picklist values (standard Zoho CRM Tasks):**

| API Value | Display |
|-----------|---------|
| `"Highest"` | Highest |
| `"High"` | High |
| `"Normal"` | Normal |
| `"Low"` | Low |
| `"Lowest"` | Lowest |

Default on new tasks: `"Normal"`. The field is optional — a task may have no priority set (field absent or empty string).

**Status picklist values:**

| API Value | Meaning |
|-----------|---------|
| `"Not Started"` | Default open state |
| `"In Progress"` | Active |
| `"Waiting for input"` | Blocked |
| `"Deferred"` | Postponed |
| `"Completed"` | Closed / done |

Confidence for status values: MEDIUM. "Completed" is the standard terminal status. Some Zoho orgs also see `"Closed"` as a synonym — check your actual org's picklist via `GET /crm/v6/settings/fields?module=Tasks` to enumerate live values. Do not hardcode only "Completed"; use a configurable set of terminal statuses.

**Owner field for filtering:**

To filter tasks assigned to a specific user, use the `Owner` filter in list queries:

```
GET /crm/v6/Tasks?fields=Subject,Description,Due_Date,Priority,Status,Owner&criteria=(Owner.id:equals:{your_user_id})
```

Your Zoho user ID can be retrieved via `GET /crm/v6/users?type=CurrentUser`. Store this at startup.

For Modified_Time incremental queries:

```
GET /crm/v6/Tasks?fields=...&criteria=((Owner.id:equals:{uid})and(Modified_Time:greater_than:{iso_timestamp}))&sort_by=Modified_Time&sort_order=ascending
```

Confidence for criteria syntax: MEDIUM. The `and` operator syntax and parenthesisation style has varied across Zoho API versions. Verify with a live test call.

---

### Rate Limits

Confidence: MEDIUM. Zoho does not publish a single canonical rate limit page — limits depend on tier and are enforced at multiple levels.

**Zoho CRM API rate limits (standard/free org):**

| Limit type | Value | Notes |
|------------|-------|-------|
| Per-minute limit | ~60 API calls/min (free); ~150/min (standard paid) | Enforced per OAuth client |
| Per-day limit | 5,000 calls/day (free org); 25,000/day (paid) | Resets at midnight IST (Zoho is India-based) |
| Burst limit | Not officially published | Backoff on HTTP 429 |

**Rate limit response:** HTTP 429 with `X-RATELIMIT-LIMIT`, `X-RATELIMIT-REMAINING`, `X-RATELIMIT-RESET` headers. The `Retry-After` header is not always present — use `X-RATELIMIT-RESET` (Unix timestamp) when available.

**Implications for this project:**

- A single webhook event = 1 fetch call + 1 write call = 2 API calls. At 60 calls/min, this handles ~30 sync events/min on free tier — well above expected load for a personal tasks sync.
- The reconciliation poller (Modified_Time filter) should run at most every 60 seconds, consuming 1-2 calls per run. At daily cadence this is ~1,440 calls/day — comfortably within free tier limits.
- If daily limit is 5,000: leave 2,000 as headroom for bursts. Alert via `/health` if daily usage exceeds 3,000.

**Confirmed safe pattern:** Incremental `Modified_Time` query is the correct approach for the reconciliation poller. Full scans are prohibited on free tier.

---

### Custom Fields

Confidence: HIGH for existence and creation pattern; MEDIUM for exact API field name after creation.

**Creating the `Todoist_Task_ID` custom field:**

Via Zoho CRM UI: Setup → Modules and Fields → Tasks → Fields → Add Custom Field → Single Line (Text).

- Label: `Todoist Task ID`
- Auto-generates API name: typically `Todoist_Task_ID` (Zoho converts spaces to underscores and appends nothing for single-line text, but may append `_c` for custom fields in some API versions).

**IMPORTANT — verify the actual API name:** After creating the field, call `GET /crm/v6/settings/fields?module=Tasks` and look for the field with `field_label: "Todoist Task ID"`. The `api_name` property gives the exact string to use in all API calls.

In some Zoho configurations, custom fields use the pattern `{Label_Without_Spaces}` (no suffix). In others they use `{Label_Without_Spaces}__c` (with double-underscore-c suffix, Salesforce-style). Do not assume — read the actual `api_name` from the settings endpoint and store it in config/env.

**Reading custom fields in task response:**

Custom fields are NOT returned by default. You must request them explicitly:

```
GET /crm/v6/Tasks/{id}?fields=Subject,Description,Due_Date,Priority,Status,Owner,Todoist_Task_ID
```

Or use `fields=all` to get everything (expensive, avoid in production polling).

**Writing custom fields:**

Same PATCH/PUT body as standard fields:

```json
{
  "data": [{
    "id": "123456789",
    "Todoist_Task_ID": "8765432109"
  }]
}
```

**Limitations:**

- Single-line text fields: max 255 characters. Todoist task IDs are numeric strings well within this limit.
- Custom fields can be hidden from standard views (correct per PROJECT.md — set "Visible in Layout" to false in the field properties).
- Cannot make custom fields required for sync purposes — this is fine since the field is absent on Zoho-only tasks until first sync.

---

### OAuth Pattern

Confidence: HIGH. This is well-documented Zoho OAuth behaviour.

**Self-client flow for background services:**

1. Go to https://api-console.zoho.com/
2. Create a "Self Client" application (for server-side scripts/daemons with no redirect URI).
3. Generate a one-time authorization code with the required scopes. Scopes needed:
   - `ZohoCRM.modules.tasks.ALL` (or split into `.READ` and `.WRITE`)
   - `ZohoCRM.settings.fields.READ` (to read field metadata at startup)
   - `ZohoCRM.users.READ` (to get current user ID at startup)
4. Exchange the one-time code for `access_token` + `refresh_token` via POST to `https://accounts.zoho.{region}/oauth/v2/token`.
5. Store `refresh_token` persistently (Postgres or env var). It does NOT expire as long as it is used at least once every 90 days on free tier (paid: longer or indefinite).

**Auto-refresh pattern:**

```python
# On any API call that returns HTTP 401:
response = await zoho_api.get(url)
if response.status_code == 401:
    new_token = await refresh_access_token(refresh_token)
    store_access_token(new_token)
    response = await zoho_api.get(url, headers={"Authorization": f"Zoho-oauthtoken {new_token}"})
```

Access tokens expire after 60 minutes (3,600 seconds). The `expires_in` field in the token response confirms this. Proactive refresh (re-fetch before expiry) is cleaner than reactive 401 handling — refresh at 50 minutes or when `expires_at - now < 120s`.

**Region matters:** Token endpoint and API base URL must match the Zoho data centre:
- US: `accounts.zoho.com`, `www.zohoapis.com`
- EU: `accounts.zoho.eu`, `www.zohoapis.eu`
- IN: `accounts.zoho.in`, `www.zohoapis.in`
- AU: `accounts.zoho.com.au`, `www.zohoapis.com.au`

The org's data centre is visible in the Zoho CRM URL when logged in. Mismatched regions produce HTTP 400 "invalid_client" errors that look like credential errors.

---

## Todoist API

### REST vs Sync API Split

Confidence: HIGH. This is stable, well-documented Todoist API architecture.

Todoist provides two separate APIs with different purposes:

| API | Base URL | Best for | Auth |
|-----|----------|----------|------|
| REST API v2 | `https://api.todoist.com/rest/v2/` | CRUD operations (create, read, update, delete tasks) | Bearer token |
| Sync API v9 | `https://api.todoist.com/sync/v9/sync` | Full/incremental state sync, `sync_token` pattern | Bearer token (same token) |

**Webhooks use REST API conventions** (payload structure matches REST API objects), but webhooks are configured separately at https://developer.todoist.com/ in the App Management Console.

**For this project:**

- **Webhook receipt** — Uses neither API client; just parse the incoming POST body.
- **Writing task updates (title, description, due date, priority)** — Use REST API v2: `POST /rest/v2/tasks/{id}` (update).
- **Completing tasks** — Use REST API v2: `POST /rest/v2/tasks/{id}/close`.
- **Deleting tasks** — Use REST API v2: `DELETE /rest/v2/tasks/{id}`.
- **Creating tasks** — Use REST API v2: `POST /rest/v2/tasks`.
- **Reconciliation poller (incremental state)** — Use Sync API v9 with `sync_token`.

The `todoist-api-python` library wraps the REST API. For Sync API calls, use `httpx` directly or the library's sync endpoint wrapper.

---

### Webhook Payload & Verification

Confidence: HIGH for payload structure; HIGH for HMAC algorithm.

**Webhook configuration:**

Webhooks are registered in the Todoist App Management Console (https://developer.todoist.com/appconsole.html). You register an endpoint URL and select which events to subscribe to.

**Events relevant to this project:**

| Event | Trigger |
|-------|---------|
| `item:added` | New task created (needed to detect Todoist-native tasks to ignore) |
| `item:updated` | Task title, description, due date, priority, or labels changed |
| `item:completed` | Task marked complete |
| `item:uncompleted` | Task un-completed |
| `item:deleted` | Task permanently deleted |

**Payload structure (all events):**

```json
{
  "event_name": "item:updated",
  "user_id": "2671355",
  "event_data": {
    "id": "2995104339",
    "project_id": "2203306141",
    "content": "Task title",
    "description": "Task description text\n\n---\n[zoho:1234567890]",
    "due": {
      "date": "2026-04-30",
      "is_recurring": false,
      "lang": "en",
      "string": "Apr 30",
      "timezone": null
    },
    "priority": 4,
    "labels": ["my_label"],
    "checked": false,
    "is_deleted": false,
    "date_added": "2026-04-01T10:00:00.000000Z",
    "date_completed": null
  },
  "initiator": {
    "email": "user@example.com",
    "full_name": "User Name",
    "id": "2671355",
    "is_premium": true
  },
  "version": "8"
}
```

For `item:completed`, `event_data.checked` is `true` and `event_data.date_completed` is an ISO 8601 string.

For `item:deleted`, `event_data.is_deleted` is `true`. The task content is included in the final payload before deletion.

**HMAC Verification:**

- Header: `X-Todoist-Hmac-SHA256`
- Algorithm: HMAC-SHA256
- Key: Your app's "Client Secret" (from the App Management Console), encoded as UTF-8 bytes
- Message: The raw request body bytes (before any JSON parsing)
- Expected value: Base64-encoded digest (standard base64, not URL-safe)

```python
import hmac
import hashlib
import base64

def verify_todoist_webhook(raw_body: bytes, client_secret: str, signature_header: str) -> bool:
    expected = base64.b64encode(
        hmac.new(
            client_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256
        ).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature_header)
```

**CRITICAL:** Read the raw body bytes BEFORE calling `request.json()` in FastAPI. FastAPI's `Request.body()` returns raw bytes; use that for HMAC, then parse separately.

**Delivery guarantee:** Todoist webhooks are at-least-once. Retries occur if your endpoint returns non-2xx. Return HTTP 200 immediately (enqueue the job) and process asynchronously — this is exactly what the arq architecture provides.

---

### Task Description Limits

Confidence: MEDIUM. Character limit not officially published in a single place; 16,384 characters is the widely-cited practical limit.

**Description field:**

- The Todoist REST API task object has a `description` field (separate from `content` which is the title).
- Practical character limit: approximately 16,384 characters. The API returns HTTP 400 for descriptions exceeding this.
- Markdown in descriptions: Todoist renders a subset of markdown (bold, italic, links, code) in its clients but does NOT strip or transform markdown when storing — the raw text is preserved exactly as submitted.
- **The `[zoho:ID]` footer will survive unchanged.** Todoist does not process, transform, or strip footer patterns. The text you write is the text you read back.

**Implication for canonical hash:** When computing the hash for loop detection, strip the footer before hashing the description. The footer is metadata, not content.

**Content field (title):**

- `content` field: max 500 characters (enforced by API, returns HTTP 400 if exceeded).
- Zoho `Subject` field: max 50 characters by default. No truncation needed in practice, but add a guard.

---

### Priority Mapping (IMPORTANT: confirm numbering)

Confidence: HIGH. Priority numbering is a well-known Todoist quirk with clear documentation.

**Todoist priority values — the numbering is COUNTER-INTUITIVE:**

| API Value | Display in App | Urgency |
|-----------|---------------|---------|
| `4` | p1 (red) | Highest / Urgent |
| `3` | p2 (orange) | High |
| `2` | p3 (blue) | Medium |
| `1` | p4 (no colour) | No priority / Natural |

**`priority: 1` = no priority (lowest). `priority: 4` = p1 (urgent/highest).**

The brief's statement "Highest→1" is WRONG if it means `priority: 1` in the API. Correct mapping:

| Zoho Priority | Todoist API `priority` | Todoist Display |
|---------------|------------------------|-----------------|
| `"Highest"` | `4` | p1 (red) |
| `"High"` | `3` | p2 (orange) |
| `"Normal"` | `2` | p3 (blue) — or consider `1` |
| `"Low"` | `1` | p4 (no colour) |
| `"Lowest"` | `1` | p4 (no colour) |
| (not set) | `1` | p4 (no colour) |

For the reverse mapping (Todoist → Zoho):

| Todoist `priority` | Zoho Priority |
|--------------------|---------------|
| `4` | `"Highest"` |
| `3` | `"High"` |
| `2` | `"Normal"` |
| `1` | `"Low"` |

Note: Zoho has 5 priority levels; Todoist has 4. The `"Lowest"` Zoho value collapses to Todoist `1` (same as `"Low"`). On the return trip, Todoist `1` maps to `"Low"` — a `"Lowest"` Zoho task becomes `"Low"` after a round-trip. This is acceptable for v1; log it as a known data loss case.

**The brief's priority table needs correction.** Search for "Highest→1" in the brief and flip the mapping: Highest → `4`, Natural → `1`.

---

### Sync Token Pattern

Confidence: HIGH. This is the core Todoist Sync API design, stable since v8.

**Full sync (startup / bootstrap):**

```
POST https://api.todoist.com/sync/v9/sync
Authorization: Bearer {token}
Content-Type: application/json

{
  "sync_token": "*",
  "resource_types": ["items"]
}
```

Response includes:
- `sync_token`: a string to use for the next incremental request
- `items`: full list of all tasks
- `full_sync`: `true`

**Incremental sync:**

```json
{
  "sync_token": "{previous_sync_token}",
  "resource_types": ["items"]
}
```

Response:
- `sync_token`: new token for next request
- `items`: only tasks changed since previous sync token
- `full_sync`: `false`

**Persistence:** Store `sync_token` in Postgres (or Redis). On service restart, load the stored token and resume incrementally. On token-not-found or corruption, fall back to `"*"` (full sync).

**Rate limits — Sync API:**

- 450 requests per 15-minute window per user token
- That is 30 req/min or 0.5 req/sec
- The reconciliation poller should run every 60 seconds = 1 call/min, well within limits
- Full sync at startup = 1 call; acceptable

**Sync token invalidation:** Zoho-triggered writes to Todoist (via REST API) count as changes. The next Sync API poll will return those items — this is where the canonical hash check suppresses the echo. The sync worker sees the Todoist item it just wrote, computes the canonical hash, matches the stored hash, and skips. This is the correct behaviour.

**Response item structure (Sync API) differs slightly from REST API:**

Sync API items use `checked: 0/1` (integer) rather than `is_completed: true/false` (boolean). The `content` and `description` fields are the same. The `due` object is the same structure.

---

## Field Mapping Validation

The following table reflects the correct mapping based on research findings. Items marked [CORRECTION] differ from the brief's assumptions.

| Concept | Zoho API Field | Todoist REST Field | Notes |
|---------|---------------|-------------------|-------|
| Title | `Subject` | `content` | Direct string copy |
| Description | `Description` | `description` (suffix `\n\n---\n[zoho:ID]`) | Strip footer before hashing |
| Due date | `Due_Date` | `due_date: "YYYY-MM-DD"` | Always normalise to date-only; never use `due_datetime` |
| Priority: Highest | `"Highest"` | `priority: 4` [CORRECTION] | Brief says →1; correct value is 4 |
| Priority: High | `"High"` | `priority: 3` [CORRECTION] | |
| Priority: Normal | `"Normal"` | `priority: 2` | |
| Priority: Low | `"Low"` | `priority: 1` | |
| Priority: Lowest | `"Lowest"` | `priority: 1` | Collapses to Low on round-trip |
| Completed | `Status == "Completed"` | `POST /tasks/{id}/close` | Check all terminal statuses, not just "Completed" |
| Owner | `Owner.id` | n/a | Filter tasks where Owner.id == current user ID |
| Zoho task ID (in Todoist) | n/a | Footer `[zoho:ID]` in `description` | Regex: `\[zoho:(\d+)\]` |
| Todoist task ID (in Zoho) | `Todoist_Task_ID` (custom field) | `id` | Verify exact API name via settings endpoint |
| Modified time | `Modified_Time` | n/a (use sync_token) | Zoho: ISO 8601 datetime; use for incremental polling |

---

## Surprises & Gotchas

### 1. Priority numbering is inverted (CRITICAL)

Todoist `priority: 1` is the lowest (no priority / p4). `priority: 4` is urgent (p1). The brief's mapping "Highest → 1" is backwards. Every priority comparison and mapping in the codebase must use the inverted scale. This is the single most likely source of a subtle bug that would survive testing because "it syncs something" — just the wrong priority.

### 2. Due_Date may arrive as datetime despite being date-only in the UI (HIGH)

The Make.com regression described in PROJECT.md is caused by Zoho returning `Due_Date` with a time component and timezone offset in some org configurations. Always parse and truncate: `due_date_str[:10]`. Never pass `due_datetime` to Todoist for tasks originating from Zoho due dates.

### 3. Zoho webhook is notification-only — always fetch (HIGH)

Confirmed in PROJECT.md. Do not attempt to optimise by extracting field values from the webhook body; there are none. The fetch is mandatory, not optional.

### 4. Custom field API name must be read from settings endpoint (MEDIUM)

Do not hardcode `Todoist_Task_ID` as the API field name without verifying it. Zoho may generate `Todoist_Task_ID__c` or another variant. Read `GET /crm/v6/settings/fields?module=Tasks` at service startup and cache the actual `api_name`. Store this as a config value, not a constant in code.

### 5. Zoho status values are org-configurable (MEDIUM)

The picklist values for `Status` (and `Priority`) can be customised per-org by a Zoho admin. The standard values are documented above, but verify your actual org's values via `GET /crm/v6/settings/fields?module=Tasks`. The "terminal" statuses that trigger Todoist completion should be configurable (env var or config), defaulting to `["Completed"]`.

### 6. Todoist Sync API items use integer `checked` not boolean `is_completed` (MEDIUM)

When processing Sync API responses in the reconciliation poller, use `item.get("checked") == 1` not `item.get("is_completed") == True`. The REST API webhooks use `checked: true/false` (boolean). Handle both forms.

### 7. Zoho data centre region (MEDIUM)

The OAuth token endpoint and API base URL must use the same regional domain as the Zoho org. A mismatch produces "invalid_client" that is easily confused with a credential error. Log the region at startup and make it an env var (`ZOHO_REGION`, default `com`).

### 8. Refresh token 90-day expiry on free tier (LOW — verify)

Zoho's documentation states that refresh tokens on free/developer orgs expire if unused for 90 days. On paid orgs this is longer or indefinite. If the service is ever stopped for >90 days, a new refresh token must be generated via the Self Client console. Consider a cron job that does a no-op API call at 30-day intervals to keep the token alive — or document this as a manual recovery step.

### 9. Todoist `item:added` vs `item:updated` on create (LOW)

When this service creates a new Todoist task (Zoho→Todoist), Todoist fires `item:added`. The webhook handler will receive this. The canonical hash check will find no stored hash (task is new from Todoist's perspective), which could cause a spurious "Todoist-native new task" code path. Ensure the Todoist_Task_ID check (or the `[zoho:ID]` footer presence) is evaluated BEFORE deciding whether to ignore an `item:added` event. Tasks created by this service have the footer; tasks created by the user do not — this is the correct ignore signal for `item:added`.

### 10. Todoist webhook delivery order not guaranteed (LOW)

If a user makes two rapid edits in Todoist, two `item:updated` webhooks arrive. Order is not guaranteed. arq job deduplication by task ID collapses these to one fetch+write, which is correct. The LWW strategy means whichever state is in Zoho at dequeue time wins — this is the documented behaviour.

### 11. `due` field in Todoist webhook may be `null` (MEDIUM)

When a due date is cleared in Todoist, `event_data.due` is `null` (not a `{"date": null}` object). Handle `due is None` explicitly when parsing webhook payloads. When writing to Zoho, a null due date should send `"Due_Date": null` (or omit the field — verify which Zoho accepts; likely `null` clears it).
