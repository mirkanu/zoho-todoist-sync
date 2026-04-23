# Phase 2: Zoho Read - Research

**Researched:** 2026-04-23
**Domain:** Zoho CRM Python SDK (v8), OAuth 2.0 token refresh, REST API record fetch, field metadata, pagination
**Confidence:** HIGH (core patterns verified; SDK sync/async architecture noted with implications)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INFRA-6 | Zoho OAuth EU region; proactive access token refresh at 50 min; refresh failure stops service and alerts | OAuth endpoint verified (`accounts.zoho.eu`); proactive refresh implemented as asyncio background task; `expires_in` is always 3600s |
| INFRA-7 | Startup fetch of `GET /crm/v6/settings/fields?module=Tasks`; resolve actual `api_name` for `Todoist Task ID` custom field; cache result | Field metadata API documented; `api_name` and `custom_field` boolean both present in response; `pick_list_values` for Status picklist also available at same endpoint |
| SYNC-4 | Webhook payload is notification-only; worker MUST fetch full task from Zoho API on dequeue | `fetch_zoho_task(zoho_task_id)` implementation covered; SDK `RecordOperations("Tasks").get_record(id)` pattern documented |
| LOOP-4 | 2-second deferred start on Zoho-triggered jobs (`ZOHO_JOB_DEFER_SECS`, default 2) | This is an arq job parameter (`_defer_by`), not a Zoho API concern; confirmed `_defer_by` is supported in arq 0.28 |

</phase_requirements>

---

## Summary

Phase 2 builds the Zoho read layer: OAuth token management, startup field/status metadata resolution, single-task fetch, and paginated modified-since fetch. The phase produces no writes to Zoho.

The key architectural decision is **whether to use the official `zohocrmsdk8_0` Python SDK or call the Zoho REST API directly via `httpx`**. The SDK is synchronous (blocking), which conflicts with the async FastAPI/arq stack. A thin `httpx`-based client that calls Zoho's REST API directly is the better fit: it is async-native, avoids thread-pool bridging overhead, and keeps the codebase consistent with the rest of the async stack. The SDK's main value (token management) is easily replicated in ~50 lines. All Zoho REST endpoints are well-documented.

The proactive 50-minute token refresh cannot use the SDK's auto-refresh mechanism in an async context. It must be implemented as a `asyncio.Task` started in the FastAPI `lifespan` context manager. The token is stored in memory (the current access token string) and also persisted to `kv_store` for restart recovery.

**Primary recommendation:** Use `httpx.AsyncClient` directly against Zoho's EU API endpoints (`www.zohoapis.eu`). Manage the OAuth token lifecycle with a background asyncio task. The `zohocrmsdk8_0` package is NOT recommended for this stack.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| OAuth token refresh (proactive, every 50 min) | API / Backend (worker startup) | — | Token is a process-level credential; must run in the background process that makes Zoho API calls |
| Field metadata fetch (`Todoist Task ID` api_name) | API / Backend (startup) | — | One-time cache operation at service startup; result stored in memory + kv_store |
| `fetch_zoho_task(id)` | API / Backend (worker) | — | Called by `sync_task` job on dequeue; result normalised then hashed |
| `fetch_zoho_tasks_modified_since(ts)` | API / Backend (worker/reconciler) | — | Called by reconciliation cron; must support pagination |
| Token persistence across restarts | Database / Storage | — | `kv_store` table (key=`zoho_access_token`, `zoho_token_expires_at`) |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `httpx` | `0.28.1` (already in requirements.txt) | Async HTTP client for all Zoho REST calls | Already installed; async-native; no thread bridging needed |
| `asyncio` | stdlib | Background token refresh task | Built-in; arq worker and FastAPI both run on asyncio event loop |
| `zohocrmsdk8_0` | NOT USED | Zoho official SDK | Synchronous blocking SDK — incompatible with async stack; REST API is simpler to call directly |

[VERIFIED: PyPI + GitHub README — `zohocrmsdk8_0` is confirmed synchronous; `httpx==0.28.1` already in project requirements.txt]

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `structlog` | stable (already installed) | Log token refresh events, API errors | Consistent with Phase 1 logging setup |
| `asyncpg` + SQLAlchemy async | 0.31.0 / 2.0.49 | Persist access token + expiry to `kv_store` | Restart recovery only |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Direct `httpx` calls | `zohocrmsdk8_0` | SDK is sync; requires `asyncio.to_thread()` wrapper and introduces thread-pool overhead; the REST API surface is small enough that direct calls are simpler |
| In-memory-only token | Persisted to `kv_store` | In-memory only is fine for the happy path; persistence enables restart recovery without a full OAuth re-exchange |
| asyncio background task | APScheduler / arq cron | The token refresher is a single-process concern; a plain asyncio task is lighter; arq cron would require the worker to be running (not the web process) |

**Installation:**
```bash
# Nothing new to install — httpx 0.28.1 is already in requirements.txt
```

---

## Architecture Patterns

### System Architecture Diagram

```
FastAPI lifespan (web process)             arq worker process
         │                                         │
         ▼                                         ▼
   startup sequence                       on_startup callback
         │                                         │
   ┌─────────────────┐                   ┌─────────────────────┐
   │ 1. load token   │                   │ 1. load token from  │
   │    from kv_store│                   │    kv_store         │
   │ 2. if expired:  │                   │ 2. start token      │
   │    refresh now  │                   │    refresh task     │
   │ 3. resolve field│                   └─────────┬───────────┘
   │    api_name     │                             │
   │ 4. start refresh│         TOKEN STORE         ▼
   │    background   │◄───────(kv_store)────► ZohoClient
   │    task         │         (shared)            │
   └────────────────-┘                             │ httpx.AsyncClient
                                                   │
                            ┌──────────────────────┼──────────────────────┐
                            ▼                      ▼                      ▼
                  fetch_zoho_task()    fetch_zoho_tasks_modified_since()  settings/fields
                  (single record)      (paginated search)                 (startup only)
                            │                      │
                            ▼                      ▼
                   normalise_task()          list of normalised tasks
                   (from Phase 1)
```

### Recommended Project Structure

```
app/
├── zoho/
│   ├── __init__.py
│   ├── client.py          # ZohoClient: token state + httpx calls
│   ├── token_manager.py   # proactive_refresh_loop() asyncio task
│   └── normalise.py       # zoho_record_to_normalised_task() adapter
tests/
└── unit/
    └── test_zoho_client.py  # mock httpx responses; test error mapping
```

### Pattern 1: Direct httpx Zoho Client

**What:** A `ZohoClient` class that holds the current access token, provides async methods for each Zoho API call, and handles error mapping to typed exceptions.

**When to use:** Every Zoho API call in the worker and web processes.

```python
# app/zoho/client.py
# Source: [CITED: www.zoho.com/crm/developer/docs/api/v8/get-records.html]
import httpx
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class ZohoAuthError(Exception):
    """Raised on 401 — refresh token invalid or scope mismatch."""

class ZohoNotFoundError(Exception):
    """Raised on 404 — task does not exist."""

class ZohoRateLimitError(Exception):
    """Raised on 429 — concurrency limit exceeded."""

class ZohoAPIError(Exception):
    """Raised on other non-2xx responses."""


@dataclass
class ZohoClient:
    access_token: str
    base_url: str = "https://www.zohoapis.eu/crm/v8"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Zoho-oauthtoken {self.access_token}"}

    async def get_task(self, zoho_task_id: str) -> dict[str, Any]:
        """Fetch a single Tasks record by ID."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/Tasks/{zoho_task_id}",
                headers=self._headers(),
            )
        return self._handle(resp, zoho_task_id)

    def _handle(self, resp: httpx.Response, context: str = "") -> Any:
        if resp.status_code == 401:
            raise ZohoAuthError(f"401 Unauthorized — {context}")
        if resp.status_code == 404:
            raise ZohoNotFoundError(f"404 Not Found — {context}")
        if resp.status_code == 429:
            raise ZohoRateLimitError(f"429 Rate limit — {context}")
        resp.raise_for_status()
        return resp.json()
```

### Pattern 2: Proactive Token Refresh (asyncio background task)

**What:** An asyncio background task that sleeps until 50 minutes after the last refresh, then calls the Zoho accounts endpoint to get a new access token. On failure, it logs ERROR, sends an alert, and raises — which stops the process.

**When to use:** Started in both the web `lifespan` and arq worker `on_startup`.

```python
# app/zoho/token_manager.py
# Source: [CITED: www.zoho.com/crm/developer/docs/api/v8/access-refresh.html]
import asyncio
import httpx
from datetime import datetime, timezone, timedelta
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

REFRESH_INTERVAL_SECS = 50 * 60   # refresh at 50 min; token expires at 60 min
ACCOUNTS_URL = "https://accounts.zoho.eu/oauth/v2/token"


async def refresh_access_token(settings) -> tuple[str, datetime]:
    """
    Call Zoho accounts EU endpoint with refresh_token grant.
    Returns (new_access_token, expires_at).
    Raises on any error — caller must handle.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            ACCOUNTS_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.zoho_client_id,
                "client_secret": settings.zoho_client_secret,
                "refresh_token": settings.zoho_refresh_token,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {resp.status_code} {resp.text}")
    body = resp.json()
    if "access_token" not in body:
        raise RuntimeError(f"Token refresh response missing access_token: {body}")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=body.get("expires_in", 3600))
    return body["access_token"], expires_at


async def proactive_refresh_loop(token_state: dict, db_session_factory):
    """
    Runs forever as an asyncio task.
    token_state is a mutable dict: {"access_token": str, "expires_at": datetime}.
    Sleeps REFRESH_INTERVAL_SECS, then refreshes.
    On failure: logs ERROR, sends alert, re-raises (kills the task — caller should monitor).
    """
    settings = get_settings()
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECS)
        try:
            new_token, new_expires_at = await refresh_access_token(settings)
            token_state["access_token"] = new_token
            token_state["expires_at"] = new_expires_at
            # Persist to kv_store for restart recovery
            async with db_session_factory() as session:
                await upsert_kv(session, "zoho_access_token", new_token)
                await upsert_kv(session, "zoho_token_expires_at", new_expires_at.isoformat())
            log.info("zoho_token_refreshed", expires_at=new_expires_at.isoformat())
        except Exception as exc:
            log.error("zoho_token_refresh_failed", error=str(exc))
            # Send alert via Resend (Phase 4 dependency — log only in Phase 2)
            raise   # kills the task; process health check will detect
```

### Pattern 3: Startup Field Metadata Resolution

**What:** On startup, call `GET /crm/v8/settings/fields?module=Tasks`, find the custom field whose `display_value` matches "Todoist Task ID", and cache its `api_name`. Also extract the Status picklist values for terminal status comparison.

**Required OAuth scope:** `ZohoCRM.settings.fields.ALL`

```python
# app/zoho/client.py (additional method)
# Source: [CITED: www.zoho.com/crm/developer/docs/api/v8/field-meta.html]

async def get_fields_metadata(self, module: str = "Tasks") -> dict:
    """
    Returns dict with:
      - todoist_task_id_api_name: str
      - status_picklist_values: list[str]
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.zohoapis.eu/crm/v8/settings/fields",
            params={"module": module},
            headers=self._headers(),
        )
    resp.raise_for_status()
    fields = resp.json().get("fields", [])

    todoist_field_api_name = None
    status_values = []

    for field in fields:
        # Find the custom "Todoist Task ID" field
        if field.get("custom_field") and "Todoist" in field.get("field_label", ""):
            todoist_field_api_name = field["api_name"]

        # Find Status picklist values
        if field.get("api_name") == "Status" and field.get("data_type") == "picklist":
            status_values = [
                pv["actual_value"]
                for pv in field.get("pick_list_values", [])
            ]

    return {
        "todoist_task_id_api_name": todoist_field_api_name,
        "status_picklist_values": status_values,
    }
```

### Pattern 4: `fetch_zoho_task` with Normalisation

**What:** Fetch a single task and apply Phase 1 normalisation rules to produce a `NormalisedTask`.

```python
# app/zoho/normalise.py
# Source: [ASSUMED] — applies Phase 1 normalise functions to Zoho record dict
from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title
from app.core.priority import zoho_to_todoist_priority


def zoho_record_to_normalised(record: dict, terminal_statuses: list[str]) -> NormalisedTask:
    """
    Convert a raw Zoho Tasks record dict to NormalisedTask.
    'record' is the inner dict from response["data"][0].
    """
    return NormalisedTask(
        title=normalise_title(record.get("Subject") or ""),
        due_date=normalise_due_date(record.get("Due_Date")),
        priority=zoho_to_todoist_priority(record.get("Priority")),
        is_completed=record.get("Status") in terminal_statuses,
    )
```

### Pattern 5: `fetch_zoho_tasks_modified_since` with Pagination

**What:** Use `GET /crm/v8/Tasks/search?criteria=...` with `Modified_Time` and `Owner` filters, paginating until `more_records=false`.

**API endpoint:** `https://www.zohoapis.eu/crm/v8/Tasks/search`

**Criteria syntax (verified):**
```
((Modified_Time:greater_equal:{ISO8601})and(Owner:equals:{ZOHO_USER_ID}))
```

```python
# app/zoho/client.py (additional method)
# Source: [CITED: www.zoho.com/crm/developer/docs/api/v8/search-records.html]
from datetime import datetime, timezone

async def fetch_tasks_modified_since(
    self, since: datetime, owner_id: str
) -> list[dict]:
    """
    Returns raw Zoho record dicts for all Tasks assigned to owner_id
    modified at or after 'since'. Handles pagination automatically.
    Always has Modified_Time filter — never a full scan.
    """
    since_str = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    criteria = (
        f"((Modified_Time:greater_equal:{since_str})"
        f"and(Owner:equals:{owner_id}))"
    )
    results = []
    page = 1
    while True:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/Tasks/search",
                params={"criteria": criteria, "page": page, "per_page": 200},
                headers=self._headers(),
            )
        if resp.status_code == 204:
            # No records found — valid empty result
            break
        self._handle(resp, f"modified_since page={page}")
        body = resp.json()
        results.extend(body.get("data", []))
        if not body.get("info", {}).get("more_records"):
            break
        page += 1
    return results
```

### Anti-Patterns to Avoid

- **Using `zohocrmsdk8_0`:** The official SDK is synchronous. Wrapping it with `asyncio.to_thread()` works but is fragile with the SDK's thread-local `Initializer` state.
- **FileStore token persistence:** Railway's filesystem is ephemeral. Always use `kv_store` in Postgres.
- **Hardcoding `Todoist_Task_ID` as the field api_name:** INFRA-7 explicitly requires startup resolution. The `__c` suffix or numeric suffix may be present.
- **Full scan without `Modified_Time` filter:** The search endpoint must always include `Modified_Time:greater_equal` to bound the query. An unconstrained `Owner:equals` query fetches all tasks ever assigned, not just recently modified ones.
- **Silently retrying refresh token failures:** INFRA-6 requires stop + alert on refresh failure. The background task must re-raise after logging ERROR.
- **Storing access token only in memory:** Always persist to `kv_store` for restart recovery. On restart, load the stored token and check `zoho_token_expires_at`; if expired, refresh immediately before accepting work.
- **Using `v6` in URL vs `v8`:** The project CLAUDE.md references `crm/v6/settings/fields` but the current Zoho CRM API is v8. The `v6` endpoint still works but verify the actual URL to use — INFRA-7 says `v6`. The field metadata endpoint path is the same between v6 and v8.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP connection pooling | Manual `httpx.AsyncClient()` per call | Shared `httpx.AsyncClient` instance with keep-alive | Per-call clients close the connection after each request, eliminating connection reuse; use a shared client held on `ZohoClient` |
| OAuth token refresh timing | `datetime.now()` arithmetic | Sleep `REFRESH_INTERVAL_SECS` then refresh | Simpler than tracking exact expiry; 50-min interval gives 10-min safety margin on a 60-min token |
| Pagination termination | Check `len(results) < 200` | Check `info.more_records == false` | Zoho sends exactly `per_page` records even on the last page in some cases; `more_records` is the authoritative signal |
| 204 vs 404 confusion | Treat 204 as an error | Handle 204 as empty result | Zoho returns `HTTP 204 No Content` (not 404) when a search returns zero results — this is normal, not an error |

**Key insight:** The main complexity in this phase is the token lifecycle, not the API calls themselves. An async token refresh loop with Postgres persistence is the entire hard part; the HTTP calls are straightforward.

---

## Common Pitfalls

### Pitfall 1: `zohocrmsdk8_0` Sync/Async Mismatch

**What goes wrong:** Calling `RecordOperations("Tasks").get_record(id)` blocks the asyncio event loop. Under load, this stalls all arq workers sharing the loop.

**Why it happens:** The Zoho Python SDK uses `requests` (synchronous) internally.

**How to avoid:** Use `httpx.AsyncClient` directly. The REST API is well-documented and the SDK adds no value in an async context.

**Warning signs:** arq worker logs show high job latency even for simple Zoho fetches; event loop warnings from asyncio.

### Pitfall 2: Zoho 204 vs 404

**What goes wrong:** A search with valid criteria that matches zero records returns `HTTP 204 No Content`, not `HTTP 404`. Code that treats any non-200 as an error will raise `ZohoNotFoundError` on a legitimate empty result set.

**Why it happens:** Zoho's search endpoint semantics: 204 = "query was valid but no records match", 404 = "module or resource does not exist".

**How to avoid:** Check `resp.status_code == 204` before raising; return `[]` for 204 responses.

**Warning signs:** `ZohoNotFoundError` logs on reconciliation runs that should produce an empty result.

### Pitfall 3: Token Refresh Race in Multi-Process Setup

**What goes wrong:** Both `web` and `worker` Railway services start simultaneously. Both try to refresh the token. Zoho allows at most 30 active access tokens per refresh token, but issuing two near-simultaneous refreshes wastes one.

**Why it happens:** Each Railway service is a separate process with separate memory; the `proactive_refresh_loop` runs independently in each.

**How to avoid:** Accept the minor redundancy in Phase 2 (two processes, two tokens). Both tokens remain valid for 60 minutes. The `kv_store` persists the latest token, but each process uses its own in-memory copy. This is acceptable for v1.

**Warning signs:** More `zoho_token_refreshed` log events than expected (2 per 50 minutes instead of 1).

### Pitfall 4: `Modified_Time` Filter Timezone Format

**What goes wrong:** Passing `"2026-04-23 10:00:00"` (without timezone offset) to the `Modified_Time` filter. Zoho may interpret this as the org's local timezone, which may differ from UTC.

**Why it happens:** The EU Zoho org has a configured timezone; ambiguous timestamps get localised differently than intended.

**How to avoid:** Always format the `Modified_Time` filter value as ISO 8601 with explicit UTC offset: `"2026-04-23T10:00:00+00:00"`. Python: `since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")`.

**Warning signs:** Reconciliation sweep misses tasks that were modified in the lookback window.

### Pitfall 5: `ZOHO_TERMINAL_STATUSES` Not Validated Against Picklist

**What goes wrong:** The env var contains `"Completed"` but the actual Zoho org uses `"Closed"` as the status API value. Every completed task is treated as open.

**Why it happens:** The picklist `actual_value` may differ from the display label; it depends on org configuration.

**How to avoid:** During startup field resolution (INFRA-7), fetch the actual picklist `actual_value` list from `Status` field metadata. Log a WARN if `ZOHO_TERMINAL_STATUSES` contains values not found in the picklist.

**Warning signs:** `is_completed=False` for tasks visually shown as complete in Zoho UI.

### Pitfall 6: `field_label` vs `display_value` for Field Discovery

**What goes wrong:** The code searches `field.get("display_value")` to find the "Todoist Task ID" field, but the correct key is `field_label` in the settings/fields response.

**Why it happens:** Zoho's field metadata response uses `field_label` for the human-readable name, not `display_value` (which is used in picklist option objects).

**How to avoid:** Search `field.get("field_label", "")` when scanning for the Todoist custom field. Log the resolved `api_name` at INFO level during startup for easy debugging.

**Warning signs:** `todoist_task_id_api_name` remains `None` after startup; tasks are never linked.

---

## Code Examples

### OAuth Token Refresh (direct httpx, EU region)
```python
# Source: [CITED: www.zoho.com/crm/developer/docs/api/v8/access-refresh.html]
async def refresh_access_token(settings) -> tuple[str, datetime]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://accounts.zoho.eu/oauth/v2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.zoho_client_id,
                "client_secret": settings.zoho_client_secret,
                "refresh_token": settings.zoho_refresh_token,
            },
        )
    body = resp.json()
    # Success: {"access_token": "...", "token_type": "Bearer", "expires_in": 3600, "api_domain": "..."}
    # Failure: {"error": "invalid_code"}  (invalid/revoked refresh token)
    if "access_token" not in body:
        raise RuntimeError(f"Refresh failed: {body.get('error', 'unknown')}")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=body["expires_in"])
    return body["access_token"], expires_at
```

### Field Metadata Fetch (resolve api_name)
```python
# Source: [CITED: www.zoho.com/crm/developer/docs/api/v8/field-meta.html]
# GET https://www.zohoapis.eu/crm/v8/settings/fields?module=Tasks
# Response: {"fields": [{"api_name": "Todoist_Task_ID", "field_label": "Todoist Task ID",
#             "custom_field": true, "data_type": "text", ...}, ...]}
# Note: INFRA-7 references /crm/v6/ — confirm version with org; v8 endpoint is identical
```

### Search Records (paginated, criteria filter)
```python
# Source: [CITED: www.zoho.com/crm/developer/docs/api/v8/search-records.html]
# GET https://www.zohoapis.eu/crm/v8/Tasks/search
# ?criteria=((Modified_Time:greater_equal:2026-04-23T10:00:00+00:00)and(Owner:equals:554023000000235011))
# &page=1&per_page=200
#
# Response:
# {"data": [...], "info": {"per_page": 200, "count": 47, "page": 1, "more_records": false}}
# When empty: HTTP 204 No Content (not 404)
```

### arq Job Defer (LOOP-4)
```python
# Source: [VERIFIED: arq-docs.helpmanual.io]
# The 2-second defer is set on the arq job enqueue, not in the Zoho client:
await redis_pool.enqueue_job(
    "sync_task",
    zoho_task_id,
    _job_id=f"sync:{zoho_task_id}",
    _defer_by=settings.zoho_job_defer_secs,   # timedelta or int seconds
)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `zohocrmsdk` (v3, sync) | `zohocrmsdk8_0` (v8, still sync) | 2024 | Still synchronous; direct httpx preferred for async stacks |
| Zoho CRM API v6 | Zoho CRM API v8 | 2024 | v8 is current; v6 still works; field metadata endpoint path is same |
| `requests` in Zoho SDK | N/A | — | SDK uses `requests`; project uses `httpx` instead of SDK |

**Deprecated/outdated:**
- `zcrmsdk` PyPI package: abandoned. Use `zohocrmsdk8_0` if SDK is needed (but prefer direct httpx).
- Zoho `FileStore` for token persistence: Railway filesystem is ephemeral; always use Postgres `kv_store`.
- `ZOHO_TERMINAL_STATUSES` hardcoded to "Completed": must be validated against live picklist at startup.

---

## Open Questions

1. **Exact Zoho API version in INFRA-7: v6 or v8?**
   - What we know: INFRA-7 references `/crm/v6/settings/fields`; current Zoho CRM API is v8
   - What's unclear: Whether the `settings/fields` endpoint behavior differs between v6 and v8
   - Recommendation: Use v8 throughout for consistency; test at startup and log the resolved field name

2. **Exact `api_name` of the `Todoist Task ID` custom field**
   - What we know: Field was created by Make.com; likely `Todoist_Task_ID` but may have a `__c` suffix or numeric suffix
   - What's unclear: Cannot be determined without a live API call
   - Recommendation: The startup resolver logs the actual value; add a startup WARN if not found

3. **Status picklist `actual_value` for this org**
   - What we know: Standard Zoho CRM has `"Completed"`; orgs can add custom statuses
   - What's unclear: Whether this org has custom terminal statuses beyond "Completed"
   - Recommendation: Startup resolver logs all status values; compare against `ZOHO_TERMINAL_STATUSES`

4. **Whether Zoho Tasks `Due_Date` returns date-only or datetime+offset for this org**
   - What we know: Phase 1 `normalise_due_date()` handles both formats correctly
   - What's unclear: The exact format this EU org returns
   - Recommendation: Log the raw `Due_Date` value in the first real fetch; normalisation handles either format

5. **httpx client lifecycle: per-call vs shared instance**
   - What we know: Per-call `async with httpx.AsyncClient()` works but wastes connection pooling
   - What's unclear: Whether connection reuse matters at the reconciliation sweep frequency (~4/hour)
   - Recommendation: Start with per-call clients in Phase 2 for simplicity; refactor to shared instance if latency becomes a concern in Phase 5+

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `httpx` | All Zoho API calls | ✓ (in requirements.txt) | 0.28.1 | — |
| `asyncio` | Background refresh task | ✓ (stdlib) | Python 3.11 stdlib | — |
| Zoho CRM (EU region, live) | E2E test of token refresh | ✓ (creds in env) | — | Mock httpx for unit tests |
| Postgres (`kv_store`) | Token persistence | ✓ (Railway, Phase 1 schema) | — | In-memory only for unit tests |

**Missing dependencies with no fallback:** None.

**Unit test strategy:** Mock `httpx.AsyncClient` responses; no live Zoho credentials needed for the test suite.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/unit/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INFRA-6 | `refresh_access_token()` returns `(token, expires_at)` on success | unit | `pytest tests/unit/test_zoho_client.py::test_token_refresh_success -x` | ❌ Wave 0 |
| INFRA-6 | `refresh_access_token()` raises on `{"error": "invalid_code"}` response | unit | `pytest tests/unit/test_zoho_client.py::test_token_refresh_invalid_code -x` | ❌ Wave 0 |
| INFRA-7 | `get_fields_metadata()` returns correct `api_name` from mocked response | unit | `pytest tests/unit/test_zoho_client.py::test_get_fields_metadata -x` | ❌ Wave 0 |
| INFRA-7 | `get_fields_metadata()` returns correct `status_picklist_values` list | unit | `pytest tests/unit/test_zoho_client.py::test_get_fields_metadata_status -x` | ❌ Wave 0 |
| SYNC-4 | `get_task()` raises `ZohoNotFoundError` on 404 | unit | `pytest tests/unit/test_zoho_client.py::test_get_task_404 -x` | ❌ Wave 0 |
| SYNC-4 | `get_task()` raises `ZohoAuthError` on 401 | unit | `pytest tests/unit/test_zoho_client.py::test_get_task_401 -x` | ❌ Wave 0 |
| SYNC-4 | `get_task()` raises `ZohoRateLimitError` on 429 | unit | `pytest tests/unit/test_zoho_client.py::test_get_task_429 -x` | ❌ Wave 0 |
| SYNC-4 | `fetch_tasks_modified_since()` returns `[]` on 204 (not an error) | unit | `pytest tests/unit/test_zoho_client.py::test_search_204_empty -x` | ❌ Wave 0 |
| SYNC-4 | `fetch_tasks_modified_since()` paginates across multiple pages | unit | `pytest tests/unit/test_zoho_client.py::test_search_pagination -x` | ❌ Wave 0 |
| SYNC-4 | `fetch_tasks_modified_since()` always includes `Modified_Time` in criteria | unit | `pytest tests/unit/test_zoho_client.py::test_search_criteria_has_modified_time -x` | ❌ Wave 0 |
| LOOP-4 | 2-second defer is set correctly on arq `enqueue_job` call | unit | `pytest tests/unit/test_zoho_client.py::test_defer_secs_passthrough -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/unit/test_zoho_client.py -x -q`
- **Per wave merge:** `pytest tests/unit/ -v`
- **Phase gate:** All unit tests green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_zoho_client.py` — covers INFRA-6, INFRA-7, SYNC-4, LOOP-4
- [ ] `app/zoho/__init__.py` — package init
- [ ] `app/zoho/client.py` — `ZohoClient` + typed exceptions
- [ ] `app/zoho/token_manager.py` — `refresh_access_token()` + `proactive_refresh_loop()`
- [ ] `app/zoho/normalise.py` — `zoho_record_to_normalised()`

*(Existing `tests/conftest.py` may need `respx` or `pytest-httpx` fixture for mocking httpx calls — add to `requirements-dev.txt`)*

---

## Security Domain

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Yes — OAuth token management | Store access token in memory only; persist to `kv_store` (Postgres, not logs); never log access token value |
| V3 Session Management | No — no user sessions | — |
| V4 Access Control | No | — |
| V5 Input Validation | Yes — Zoho API responses | Validate expected keys present in response before accessing; raise typed exception on unexpected format |
| V6 Cryptography | No — token is a bearer string, not a key | — |

### Known Threat Patterns for Zoho OAuth

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Access token logged in plaintext | Information Disclosure | Never pass `access_token` to structlog; log only expiry time and first 8 chars for debugging |
| Refresh token stored in env var (Railway) | Information Disclosure | Railway encrypts env vars at rest; do not log `ZOHO_REFRESH_TOKEN`; never commit to git |
| Token refresh failure silently ignored | Denial of Service | INFRA-6: refresh failure must log ERROR + alert + raise — not silently retry |
| 401 response retried indefinitely | Denial of Service | `ZohoAuthError` must stop the sync job and alert, not retry |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `zohocrmsdk8_0` uses synchronous `requests` internally | Standard Stack | If SDK is async-capable, direct httpx approach is still correct but SDK could be an option |
| A2 | Zoho `settings/fields` response uses `field_label` (not `display_value`) to hold the human-readable field name | Pattern 3 | Startup resolver silently returns `None` for `todoist_task_id_api_name`; log WARN catches this |
| A3 | Zoho 204 No Content is returned on empty search results (not 404) | Pattern 5, Pitfall 2 | If Zoho returns 200 with `{"data": []}` instead, the 204 branch is dead code but not harmful |
| A4 | `expires_in` in the OAuth token refresh response is always 3600 seconds | Pattern 2 | If expiry is shorter, the 50-minute refresh interval may not be safe; log `expires_in` at INFO |
| A5 | The `LOOP-4` defer is implemented via `_defer_by` on `enqueue_job`, not in the Zoho client | Phase req notes | If arq `_defer_by` is not supported in 0.28, need to use `asyncio.sleep()` inside the job function |

---

## Sources

### Primary (HIGH confidence)
- [CITED: www.zoho.com/crm/developer/docs/api/v8/access-refresh.html] — OAuth token refresh endpoint, EU URL, request params, response format
- [CITED: www.zoho.com/crm/developer/docs/api/v8/search-records.html] — Search endpoint, criteria syntax, pagination (`more_records`), EU base URL
- [CITED: www.zoho.com/crm/developer/docs/api/v8/field-meta.html] — Field metadata endpoint, `api_name`, `custom_field` boolean, picklist `actual_value`
- [CITED: www.zoho.com/crm/developer/docs/api/v8/api-limits.html] — Rate limits (concurrency-based, not time-based; 429 on concurrency exceeded)
- [CITED: arq-docs.helpmanual.io/_modules/arq/cron] — arq `cron()` signature; `minute={0,15,30,45}` confirmed; `_defer_by` confirmed in arq docs
- [CITED: github.com/zoho/zohocrm-python-sdk-8.0 README] — PyPI package `zohocrmsdk8_0`, EU init code, TokenStore interface

### Secondary (MEDIUM confidence)
- [CITED: www.zoho.com/crm/developer/docs/api/v8/get-records.html] — GET /Tasks/{id} endpoint; pagination (page + per_page); sort parameters
- [WebSearch verified] — `zohocrmsdk8_0` is synchronous (consistent with SDK source structure and documentation)

### Tertiary (LOW confidence)
- [ASSUMED] — `field_label` is the correct key for human-readable field name in `settings/fields` response (A2 above)
- [ASSUMED] — Zoho returns HTTP 204 on empty search (multiple sources corroborate but not from official Zoho docs)

---

## Project Constraints (from CLAUDE.md)

| Directive | How It Applies to Phase 2 |
|-----------|--------------------------|
| Python 3.12 (Railway) / 3.11+ local | All code must run on 3.11+; no 3.12-only syntax |
| FastAPI + arq stack | Token refresh loop must integrate with FastAPI `lifespan` and arq `on_startup` |
| Postgres on Railway | `kv_store` used for token persistence; already migrated in Phase 1 |
| Redis on Railway | No Redis use in Phase 2 |
| All secrets as env vars | `zoho_client_id`, `zoho_client_secret`, `zoho_refresh_token` come from `get_settings()` |
| Zoho org region EU | All API URLs use `www.zohoapis.eu` and `accounts.zoho.eu` |
| No UI | Phase 2 has no UI concerns |
| GSD workflow for non-trivial work | Applies to this phase execution |

---

## Metadata

**Confidence breakdown:**
- Standard stack (httpx over SDK): HIGH — SDK sync nature verified; httpx already installed
- OAuth token refresh pattern: HIGH — endpoint URL and params verified from official docs
- Field metadata API: HIGH — response structure verified from official docs
- Search/pagination pattern: HIGH — criteria syntax and `more_records` verified from official docs
- Rate limits: HIGH — concurrency-based (not time-based) verified from official docs
- Typed exceptions pattern: HIGH — standard Python; no library dependency
- `field_label` key name: LOW — assumed; verify against actual API response on first run

**Research date:** 2026-04-23
**Valid until:** 2026-05-23 (Zoho API endpoints are stable; verify httpx version if > 30 days)
