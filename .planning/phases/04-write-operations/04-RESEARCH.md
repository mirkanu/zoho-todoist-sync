# Phase 4: Write Operations - Research

**Researched:** 2026-04-24
**Domain:** Todoist REST API v1 write paths (SDK), Zoho CRM v8 REST write paths (raw httpx), Resend async email, idempotency patterns
**Confidence:** HIGH (SDK methods verified from installed source; Zoho REST write endpoint verified from official docs; Resend async pattern verified from SDK source)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SYNC-1 | Zoho tasks appear in Todoist within 60s | `create_todoist_task()` via `TodoistAPIAsync.add_task()` — confirmed correct method signature |
| SYNC-2 | Zoho→Todoist mapping; due_date YYYY-MM-DD, priority 1–4 | `add_task(due_date=date.fromisoformat(…))` passes `date` object; SDK formats as YYYY-MM-DD via `format_date()`. `due_datetime` must NOT be used |
| SYNC-3 | Todoist→Zoho mapping; completion propagation | Zoho update via `PUT /crm/v8/Tasks/{id}` with `{"data":[{"Status":"Completed"}]}`; priority reverse-map via `todoist_to_zoho_priority()` |
| SYNC-6 | Zoho custom field `Todoist Task ID` holds Todoist ID | Write-back via `PUT /crm/v8/Tasks/{id}` including `{zoho_todoist_task_id_field: task.id}` |
| SYNC-8 | Footerless tasks discarded; footer appended on create | Footer `\n\n---\n[zoho:{ZOHO_TASK_ID}]` embedded in `description` arg to `add_task()` |
| EDGE-1 | Task reassigned away → delete Todoist task + Resend email | `TodoistAPIAsync.delete_task()` + `resend.Emails.send_async()` |
| EDGE-2 | Todoist task deleted → delete Zoho task + Resend email | `DELETE /crm/v8/Tasks/{id}` + `resend.Emails.send_async()` |
| EDGE-3 | Null due date propagation in both directions | **CRITICAL PITFALL**: SDK `update_task(due_date=None)` is silently dropped by `kwargs_without_none`. Must use `due_string="no date"` via raw httpx to clear Todoist due. Zoho: `{"Due_Date": null}` in PUT body |
| EDGE-4 | `ZOHO_TERMINAL_STATUSES` env var for completion | `settings.zoho_terminal_statuses_list` already exists; use first terminal status when writing back to Zoho |
| EDGE-6 | Resend failure does not roll back deletion | Try/except around `resend.Emails.send_async()`; log error to `sync_events`, do not re-raise |
| EDGE-7 | Zoho completion triggers Todoist close | `TodoistAPIAsync.complete_task(task_id)` → `POST /tasks/{id}/close` |

</phase_requirements>

---

## Summary

Phase 4 builds the write layer: six write functions for Todoist (create, update, complete, delete, and write-back the Zoho task ID) and four for Zoho (update, complete, delete). All functions are standalone async functions in `app/todoist/writer.py` and `app/zoho/writer.py`. The pattern mirrors the read layer (Phase 2/3): raw httpx for Zoho, SDK for Todoist where SDK supports it.

The most important discovery is the **SDK due-date clearing trap**: `TodoistAPIAsync.update_task()` uses `kwargs_without_none()` internally, so passing `due_date=None` silently drops the field instead of clearing it. Clearing a Todoist due date requires passing `due_string="no date"` via the SDK's `due_string` parameter — this is the standard Todoist natural-language string that removes the date. This is the only edge-case write path that deviates from the SDK.

Resend 2.29.0 ships `resend.Emails.send_async()` for non-blocking email sending. Failure must be caught and logged without rolling back any prior deletion (EDGE-6).

For Zoho write operations: the project already uses `httpx.AsyncClient` directly (no Zoho SDK); the same pattern applies for PUT (update), PUT with `{"Status": terminal_status}` (complete), DELETE (delete), and the custom field write-back. Zoho's `Due_Date` field accepts `null` as a JSON value to clear it — passed as `{"Due_Date": None}` in the httpx JSON body (Python `None` serialises to JSON `null`).

**Primary recommendation:** SDK `add_task()` / `update_task()` / `complete_task()` / `delete_task()` for Todoist; raw httpx `PUT` / `DELETE` for Zoho; `resend.Emails.send_async()` wrapped in try/except for notifications. No new dependencies required.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| `create_todoist_task(zoho_task)` | API / Backend (worker) | — | Called by `sync_task` job when no Todoist ID exists for a Zoho task |
| `update_todoist_task(task_id, normalised)` | API / Backend (worker) | — | Applies Zoho changes to Todoist; omits description/labels |
| `complete_todoist_task(task_id)` | API / Backend (worker) | — | Triggered by Zoho terminal status change |
| `delete_todoist_task(task_id)` | API / Backend (worker) | — | Triggered when Zoho task is reassigned away (EDGE-1) |
| `update_zoho_task(zoho_task_id, normalised)` | API / Backend (worker) | — | Applies Todoist changes to Zoho; uses PUT with synced fields only |
| `complete_zoho_task(zoho_task_id)` | API / Backend (worker) | — | Triggered by Todoist `item:completed` event |
| `delete_zoho_task(zoho_task_id)` | API / Backend (worker) | — | Triggered when Todoist task is deleted (EDGE-2) |
| `write_todoist_id_to_zoho(zoho_task_id, todoist_task_id)` | API / Backend (worker) | Database | Writes Todoist ID into Zoho custom field after creating; enables ID linkage for SYNC-6 |
| Resend email notification | API / Backend (worker) | — | Fire-and-forget; failure logged only (EDGE-6) |
| Footer construction | API / Backend | — | Pure function: `f"\n\n---\n[zoho:{zoho_task_id}]"` |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `todoist-api-python` | 4.0.0 (installed) | `add_task`, `update_task`, `complete_task`, `delete_task` | Already installed; `TodoistAPIAsync` already used in Phase 3; SDK handles URL construction and JSON serialisation correctly |
| `httpx` | 0.28.1 (installed) | Zoho write calls (PUT, DELETE); Todoist due-date clear override | Already installed; same pattern as `ZohoClient` in Phase 2 |
| `resend` | 2.29.0 (installed) | `resend.Emails.send_async()` for deletion notifications | Already in requirements.txt; supports async natively via `[async]` extra; confirmed installed |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `app.core.priority` | (project module) | `todoist_to_zoho_priority()` for reverse mapping | Todoist→Zoho priority write |
| `app.core.normalise` | (project module) | `normalise_due_date()` for round-trip safety | Construct Zoho `Due_Date` value from normalised form |
| `app.zoho.state` | (project module) | `zoho_field_cache["todoist_task_id_api_name"]` | Dynamic field API name for write-back |
| `app.core.config` | (project module) | `settings.zoho_terminal_statuses_list[0]` | Completion status string (not hardcoded "Completed") |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| SDK `update_task()` for due-date clear | Raw httpx POST to `/api/v1/tasks/{id}` | SDK strips `None` values; raw httpx needed only for the `due_string="no date"` clear case — calling raw httpx for ALL updates would duplicate the URL-construction logic that the SDK handles correctly |
| `resend.Emails.send_async()` | `httpx.AsyncClient` POST to Resend API | SDK is already installed and provides typed params; no benefit to hand-rolling |

**Installation:**
```bash
# Nothing new to install — all three libraries are already in requirements.txt
```

---

## Architecture Patterns

### System Architecture Diagram

```
  sync_task job (Phase 5 caller)
           │
           ▼
  ┌────────────────────────────────────────────────┐
  │  Writer Layer (Phase 4)                        │
  │                                                │
  │  app/todoist/writer.py                         │
  │  ┌─────────────────────────────────────────┐   │
  │  │ create_todoist_task(zoho_task)           │   │
  │  │   → add_task(content, description=footer│   │
  │  │              due_date, priority,         │   │
  │  │              project_id)                 │   │
  │  │   ← returns todoist_task_id (str)        │   │
  │  │                                          │   │
  │  │ update_todoist_task(task_id, normalised) │   │
  │  │   due_date is not None → SDK update_task │   │
  │  │   due_date is None     → raw httpx POST  │   │
  │  │                          due_string="no  │   │
  │  │                          date"           │   │
  │  │                                          │   │
  │  │ complete_todoist_task(task_id)           │   │
  │  │   → SDK complete_task(task_id)           │   │
  │  │                                          │   │
  │  │ delete_todoist_task(task_id)             │   │
  │  │   → SDK delete_task(task_id)             │   │
  │  │   → resend.send_async() [fire+forget]    │   │
  │  └─────────────────────────────────────────┘   │
  │                                                │
  │  app/zoho/writer.py                            │
  │  ┌─────────────────────────────────────────┐   │
  │  │ update_zoho_task(zoho_id, normalised)    │   │
  │  │   → PUT /crm/v8/Tasks/{id}              │   │
  │  │     {Subject, Due_Date, Priority}        │   │
  │  │     Due_Date: "YYYY-MM-DD" or null       │   │
  │  │                                          │   │
  │  │ complete_zoho_task(zoho_id)              │   │
  │  │   → PUT /crm/v8/Tasks/{id}              │   │
  │  │     {Status: terminal_statuses_list[0]}  │   │
  │  │                                          │   │
  │  │ delete_zoho_task(zoho_id)               │   │
  │  │   → DELETE /crm/v8/Tasks/{id}           │   │
  │  │   → resend.send_async() [fire+forget]    │   │
  │  │                                          │   │
  │  │ write_todoist_id_to_zoho(z_id, t_id)    │   │
  │  │   → PUT /crm/v8/Tasks/{id}              │   │
  │  │     {todoist_task_id_field: t_id}        │   │
  │  └─────────────────────────────────────────┘   │
  └────────────────────────────────────────────────┘
           │
           ▼ (typed exceptions propagate to caller)
  TodoistAuthError / ZohoAuthError    → stop sync + alert
  TodoistNotFoundError / ZohoNotFoundError → log, return
  TodoistRateLimitError / ZohoRateLimitError → arq retry
```

### Recommended Project Structure

```
app/
├── todoist/
│   ├── client.py          # Phase 3: read (fetch_todoist_task, fetch_sync_delta)
│   ├── normalise.py       # Phase 3: extract_zoho_id, todoist_task_to_normalised
│   ├── sync_manager.py    # Phase 3: sync_token persistence
│   └── writer.py          # Phase 4 NEW: create/update/complete/delete
├── zoho/
│   ├── client.py          # Phase 2: read (get_task, get_fields_metadata, fetch_modified)
│   ├── normalise.py       # Phase 2: zoho_record_to_normalised
│   ├── state.py           # Phase 2: token_state, zoho_field_cache
│   ├── token_manager.py   # Phase 2: refresh + proactive loop
│   └── writer.py          # Phase 4 NEW: update/complete/delete/write_todoist_id
└── core/
    ├── config.py           # Settings (terminal_statuses_list, todoist_project_id)
    ├── hash.py
    ├── normalise.py        # NormalisedTask, strip_footer, normalise_due_date
    └── priority.py         # zoho_to_todoist_priority, todoist_to_zoho_priority
```

### Pattern 1: Todoist Write via SDK (create / complete / delete)

Standard path — SDK handles URL and serialisation. Wrap `httpx.HTTPStatusError` into typed exceptions (same pattern as Phase 3's `fetch_todoist_task`).

```python
# Source: /data/home/.local/lib/python3.11/site-packages/todoist_api_python/api_async.py
# add_task signature (lines 241-326)
task = await self._api.add_task(
    content=normalised_task.title,
    description=f"\n\n---\n[zoho:{zoho_task_id}]",
    project_id=settings.todoist_project_id,
    due_date=date.fromisoformat(normalised_task.due_date) if normalised_task.due_date else None,
    priority=normalised_task.priority,
)
# Returns todoist_api_python.models.Task with .id (str)

# complete_task (lines 451-470) — POST /tasks/{id}/close
success = await self._api.complete_task(task_id)

# delete_task (lines 536-552) — DELETE /tasks/{id}
success = await self._api.delete_task(task_id)
```

### Pattern 2: Todoist Due-Date Clear (SDK bypass required)

The SDK `update_task()` uses `kwargs_without_none()` which drops `None` values — passing `due_date=None` silently does nothing. To clear a Todoist due date, pass `due_string="no date"` through the SDK. This is the Todoist-recognised natural language string for removing due dates.

```python
# Source: [VERIFIED from installed SDK source _core/utils.py line 50-52]
# kwargs_without_none strips None keys — so due_date=None is ignored by update_task()

# WRONG — silently has no effect:
await self._api.update_task(task_id, due_date=None)

# CORRECT — clears the due date:
await self._api.update_task(task_id, due_string="no date")

# For non-null due date updates (standard path):
await self._api.update_task(
    task_id,
    content=normalised.title,
    due_date=date.fromisoformat(normalised.due_date),  # date object, not string
    priority=normalised.priority,
    # NOTE: labels parameter intentionally omitted (SYNC-9)
    # NOTE: description parameter intentionally omitted (SYNC-7)
)
```

### Pattern 3: Zoho Write via Raw httpx (PUT / DELETE)

Same `ZohoClient`-style pattern as Phase 2. The PUT body uses `{"data": [{field: value}]}` shape. `None` for `Due_Date` serialises to JSON `null` via `httpx`'s `json=` parameter, which Zoho accepts as a field-clear.

```python
# Source: [VERIFIED: https://www.zoho.com/crm/developer/docs/api/v8/update-records.html]
async with httpx.AsyncClient() as client:
    resp = await client.put(
        f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_task_id}",
        headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
        json={
            "data": [{
                "Subject": normalised.title,
                "Due_Date": normalised.due_date,  # "YYYY-MM-DD" or None (→ JSON null)
                "Priority": todoist_to_zoho_priority(normalised.priority),
            }]
        },
    )
# HTTP 207 = partial success — check response body for per-record status

# Complete:
json={"data": [{"Status": settings.zoho_terminal_statuses_list[0]}]}

# Delete: DELETE /crm/v8/Tasks/{id}
# No body required; 204 = success, 404 = already gone (idempotent)

# Write-back Todoist ID:
json={"data": [{zoho_field_cache["todoist_task_id_api_name"]: todoist_task_id}]}
```

### Pattern 4: Resend Email (async, fire-and-forget)

```python
# Source: [VERIFIED: resend 2.29.0 installed at /data/home/.local/lib/python3.11/site-packages/resend/]
import resend

resend.api_key = settings.resend_api_key

async def send_deletion_notification(subject: str, body: str) -> None:
    try:
        params: resend.Emails.SendParams = {
            "from": "sync-alerts@yourdomain.com",  # [ASSUMED] sender domain — must match verified Resend domain
            "to": ["manuelkuhs@gmail.com"],
            "subject": subject,
            "html": body,
        }
        await resend.Emails.send_async(params)
    except Exception as exc:
        log.error("resend_email_failed", error=str(exc))
        # Do NOT re-raise — EDGE-6: Resend failure does not roll back deletion
```

### Pattern 5: Zoho 207 Multi-Status Response

Zoho's update/delete endpoints return HTTP 207 when a batch request has partial success. Since this project sends single-record batches (`"data": [one_record]`), 207 should not occur in practice, but the writer must handle it gracefully.

```python
# Pattern: check status + response body
if resp.status_code == 207:
    # Extract per-record status from response body
    body = resp.json()
    record_result = body.get("data", [{}])[0]
    if record_result.get("status") != "success":
        raise ZohoAPIError(f"Zoho 207 partial failure: {record_result}")
```

### Anti-Patterns to Avoid

- **Passing `due_datetime` to Todoist `add_task`:** Always use `due_date` (a `date` object, not `datetime`). `due_datetime` triggers timezone-aware scheduling in Todoist which is never what we want (SYNC-2).
- **Hardcoding `"Completed"` as Zoho status:** Always read from `settings.zoho_terminal_statuses_list[0]` (EDGE-4).
- **Passing `labels=...` to `update_task`:** The SDK's `update_task` accepts `labels` — never pass it. Only set `content`, `due_date`/`due_string`, and `priority` (SYNC-9).
- **Ignoring Zoho `write_todoist_id_to_zoho` failure:** This write-back is critical for SYNC-6 linkage. If it fails, the `sync_state` row must NOT be committed — raise and retry.
- **Setting `api_key` globally in resend module:** `resend.api_key = ...` mutates a module-level variable. Set it once at startup (lifespan) or pass via params, not per-call. In tests, reset with `resend.api_key = "test-key"`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Todoist task URL construction | `f"https://api.todoist.com/api/v1/tasks/{id}"` | `get_api_url(f"{TASKS_PATH}/{id}")` via SDK | SDK already has correct base URL and version |
| Zoho priority string↔int mapping | Custom dict | `app.core.priority.todoist_to_zoho_priority()` | Already implemented and tested in Phase 1 |
| Due date string formatting | `datetime.strftime` | `date.fromisoformat(normalised.due_date)` — SDK formats with `format_date()` | SDK format_date produces correct YYYY-MM-DD |
| Footer construction | Complex template | `f"\n\n---\n[zoho:{zoho_task_id}]"` | Already specified in SYNC-5; simple f-string is correct |
| KV upsert for ID persistence | Custom DB logic | `upsert_kv` from `app.zoho.token_manager` | Already implemented and tested; import directly |
| Error classification | Custom status-code checks | Extend existing `_handle()` / `_raise_typed()` pattern | Typed exceptions already established in Phases 2 and 3 |

**Key insight:** The Todoist SDK already handles all URL construction, JSON serialisation, and auth headers correctly for write operations. The only case that bypasses the SDK is clearing due dates (due_string="no date"), because the SDK's `kwargs_without_none` makes `None` semantically unreachable.

---

## Common Pitfalls

### Pitfall 1: SDK Silently Drops `due_date=None` in `update_task`

**What goes wrong:** `await api.update_task(task_id, due_date=None)` compiles and runs with no error but the due date is not cleared. The next reconciliation cycle sees a mismatch and re-runs the update — infinite loop of no-op updates.

**Why it happens:** `TodoistAPIAsync.update_task()` builds its payload with `kwargs_without_none(due_date=...)` which drops any key whose value is `None`. The POST body simply never includes `due_date`.

**How to avoid:** When `normalised.due_date is None`, pass `due_string="no date"` to `update_task()` instead of `due_date=None`. This is the designated Todoist mechanism for removing due dates.

**Warning signs:** Reconciliation log shows repeated `action='sync'` for the same task with no actual field change; `due_date` field in `sync_state.last_hash` shows `null` but Todoist task still has a due date.

### Pitfall 2: Zoho HTTP 207 Mistaken for Success

**What goes wrong:** Writer checks only `resp.status_code == 200` and considers HTTP 207 a success, missing the per-record failure buried in the response body.

**Why it happens:** Zoho uses HTTP 207 Multi-Status for batch responses even when only one record was sent.

**How to avoid:** Check `resp.status_code in (200, 201)` for success and handle 207 by reading `body["data"][0]["status"]`. A result status other than `"success"` is an error.

### Pitfall 3: `write_todoist_id_to_zoho` Uses Stale `api_name`

**What goes wrong:** The Todoist task ID is written to the wrong Zoho field (or not at all) because `zoho_field_cache["todoist_task_id_api_name"]` is `None` (field not resolved at startup).

**Why it happens:** Field resolution happens at startup; if it fails silently, `zoho_field_cache` is never populated.

**How to avoid:** The Phase 2 lifespan already logs WARN if field is not found. In `write_todoist_id_to_zoho`, guard: if `zoho_field_cache.get("todoist_task_id_api_name")` is `None`, raise `ZohoAPIError("todoist_task_id_api_name not resolved — cannot write ID linkage")`. This makes the `create_todoist_task` round-trip fail fast rather than creating an unlinked Todoist task.

### Pitfall 4: Resend `api_key` Module-Level Mutation in Tests

**What goes wrong:** Tests that import `resend` accidentally share the global `resend.api_key` state between tests, causing one test's mock key to leak into another test.

**Why it happens:** `resend.api_key = ...` sets a module-level variable. If tests run in any order other than the one the author expected, keys bleed through.

**How to avoid:** In tests, always reset `resend.api_key` in the test setup or use `monkeypatch.setattr(resend, "api_key", "re_test_key")`. Never rely on the module import order.

### Pitfall 5: Double Email on Idempotent Delete Re-call

**What goes wrong:** `delete_todoist_task` is called twice (e.g., the arq job retried after a transient DB error after the delete succeeded). The task is already gone (Todoist returns 404 on second delete), but the email notification fires again.

**Why it happens:** No dedup guard on the email; each function call triggers a Resend call before checking the delete result.

**How to avoid:** Send the email AFTER the delete call succeeds, not before. Treat `TodoistNotFoundError` on delete as "already deleted — idempotent success"; do NOT send a second email. Pattern:
```python
try:
    await api.delete_task(task_id)
except TodoistNotFoundError:
    log.info("todoist_delete_idempotent", task_id=task_id)
    return  # already gone — no email
# Only reach here if delete succeeded:
await send_deletion_notification(...)
```

### Pitfall 6: Including `description` in `update_todoist_task`

**What goes wrong:** `update_task(task_id, description=new_text)` overwrites the `[zoho:ID]` footer with whatever the description arg contains.

**Why it happens:** The SDK's `update_task` accepts a `description` parameter. Passing `None` silently drops it (kwargs_without_none), but if a developer mistakenly passes `description=some_value`, the footer is destroyed.

**How to avoid:** The `update_todoist_task` function must NEVER pass `description` to `update_task`. The description field is only written once on creation (with the footer). Subsequent updates touch `content`, `due_date`/`due_string`, and `priority` only.

---

## Code Examples

Verified patterns from installed SDK source and official Zoho v8 docs:

### Create Todoist Task (Zoho→Todoist)

```python
# Source: [VERIFIED: api_async.py lines 241-326]
from datetime import date

async def create_todoist_task(
    zoho_task: dict,
    normalised: NormalisedTask,
    zoho_task_id: str,
    settings: Settings,
    todoist_api: TodoistAPIAsync,
) -> str:
    """Returns the new Todoist task ID (str)."""
    due = date.fromisoformat(normalised.due_date) if normalised.due_date else None
    try:
        task = await todoist_api.add_task(
            content=normalised.title,
            description=f"\n\n---\n[zoho:{zoho_task_id}]",
            project_id=settings.todoist_project_id,
            due_date=due,  # date object; SDK formats to YYYY-MM-DD
            priority=normalised.priority,
            # labels intentionally omitted (SYNC-9)
        )
    except httpx.HTTPStatusError as exc:
        _raise_typed(exc.response.status_code, f"add_task zoho:{zoho_task_id}", exc)
    return task.id
```

### Update Todoist Task

```python
# Source: [VERIFIED: api_async.py lines 370-449 + utils.py lines 50-52]
async def update_todoist_task(
    task_id: str,
    normalised: NormalisedTask,
    todoist_api: TodoistAPIAsync,
) -> None:
    kwargs: dict = {
        "content": normalised.title,
        "priority": normalised.priority,
        # description intentionally NEVER passed (SYNC-7 / Pitfall 6)
        # labels intentionally NEVER passed (SYNC-9)
    }
    if normalised.due_date is not None:
        kwargs["due_date"] = date.fromisoformat(normalised.due_date)
    else:
        kwargs["due_string"] = "no date"  # CRITICAL: SDK cannot clear via due_date=None
    try:
        await todoist_api.update_task(task_id, **kwargs)
    except httpx.HTTPStatusError as exc:
        _raise_typed(exc.response.status_code, f"update_task {task_id}", exc)
```

### Update Zoho Task (raw httpx)

```python
# Source: [VERIFIED: https://www.zoho.com/crm/developer/docs/api/v8/update-records.html]
from app.core.priority import todoist_to_zoho_priority

async def update_zoho_task(
    zoho_task_id: str,
    normalised: NormalisedTask,
    access_token: str,
) -> None:
    payload = {
        "Subject": normalised.title,
        "Due_Date": normalised.due_date,  # "YYYY-MM-DD" or None → JSON null
        "Priority": todoist_to_zoho_priority(normalised.priority),
    }
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_task_id}",
            headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
            json={"data": [payload]},
        )
    _zoho_handle(resp, f"PUT /Tasks/{zoho_task_id}")
```

### Zoho Write-Back Todoist ID

```python
async def write_todoist_id_to_zoho(
    zoho_task_id: str,
    todoist_task_id: str,
    access_token: str,
    field_api_name: str,
) -> None:
    if not field_api_name:
        raise ZohoAPIError("todoist_task_id_api_name not resolved")
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{ZOHO_EU_BASE_URL}/Tasks/{zoho_task_id}",
            headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
            json={"data": [{field_api_name: todoist_task_id}]},
        )
    _zoho_handle(resp, f"PUT /Tasks/{zoho_task_id} write_todoist_id")
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Todoist REST API v2 `PATCH /tasks/{id}` | Todoist API v1 `POST /tasks/{id}` (POST for both create AND update) | API v1 launch 2024 | SDK handles this; no action required — the installed SDK already targets v1 |
| Zoho CRM SDK (synchronous) | Direct httpx against Zoho v8 REST | Phase 2 decision | Already established; write endpoints follow same pattern |
| Todoist Sync API write commands (`item_add`, `item_update`) | REST API for writes | Todoist v1 unification | REST is preferred for writes; Sync API write commands are an alternative but add temp_id complexity |

**Deprecated/outdated:**
- Todoist `close` endpoint name: The SDK method is `complete_task()` not `close_task()` — internally calls `POST /tasks/{id}/close`. Do not use `close` as a function name.
- Zoho v6 write endpoints: The project uses v8 (`/crm/v8/`). The v8 base URL is already set in `app/zoho/client.py`.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Zoho accepts `{"Due_Date": null}` in a PUT body to clear the date field | Pattern 3, Pitfall 1 | Zoho may require omitting the key entirely or using a sentinel string like `""` — would need to test against live Zoho or change to omit the key when None |
| A2 | `due_string="no date"` is the correct Todoist natural-language string to clear a due date | Pattern 2, Pitfall 1 | Alternate strings like `"no due date"` may also work; if this fails, raw httpx POST with `{"due_string": "no date"}` in json body (same field name) is the fallback |
| A3 | Resend sender domain is already verified in the Resend account | Pattern 4 | If the `from` domain is not verified, Resend will reject the send with a 422 error — the error is caught and logged per EDGE-6, so the service continues but notifications won't be delivered |
| A4 | Zoho DELETE `/crm/v8/Tasks/{id}` returns 204 on success and 404 if already deleted | Architecture Patterns | Zoho may return 200 with a status body instead of 204 — handler must accept both 2xx responses |

---

## Open Questions

1. **Zoho null Due_Date clearing**
   - What we know: Zoho docs show `{"Due_Date": null}` is standard JSON null; the httpx `json=` parameter serialises Python `None` as JSON `null`
   - What's unclear: Whether Zoho's Tasks module specifically accepts null to clear a date vs. requiring an empty string `""`
   - Recommendation: Tag as A1 (ASSUMED); implement with `None` and verify in the first integration test

2. **Resend sender domain**
   - What we know: `manuelkuhs@gmail.com` is the recipient; Resend requires a verified sender domain
   - What's unclear: Which verified domain is configured in the Resend account
   - Recommendation: Look up the verified Resend domain from the Resend dashboard before Phase 8 (email is part of deletion handling, not daily operation — acceptable to defer exact sender address)

3. **Zoho DELETE response code**
   - What we know: Zoho docs show DELETE returns success for valid deletions; 404 for not-found
   - What's unclear: Whether success is HTTP 200 with a JSON body or HTTP 204 with no body
   - Recommendation: Implement `_zoho_handle` to accept all 2xx codes; log the status code on success

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `todoist-api-python` | All Todoist write ops | Yes | 4.0.0 | — |
| `httpx` | Zoho write ops, Todoist due-date clear | Yes | 0.28.1 | — |
| `resend` | Deletion email notifications | Yes | 2.29.0 | — |
| `resend[async]` extra | `send_async()` | Yes (verified installed) | 2.29.0 | Fall back to `send()` in a thread via `asyncio.to_thread()` |

**Missing dependencies:** None — all required libraries are installed.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (already configured) |
| Config file | `pyproject.toml` — `asyncio_mode = "auto"` |
| Quick run command | `python -m pytest tests/unit/test_todoist_writer.py tests/unit/test_zoho_writer.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SYNC-1/2 | `create_todoist_task` appends footer, uses due_date not due_datetime, maps priority | unit | `pytest tests/unit/test_todoist_writer.py::test_create_task_payload -x` | ❌ Wave 0 |
| SYNC-2 | Priority mapping Highest→4 round-trips correctly in create/update | unit | `pytest tests/unit/test_todoist_writer.py::test_create_priority_mapping -x` | ❌ Wave 0 |
| SYNC-3 | `update_zoho_task` sends correct Subject/Due_Date/Priority | unit | `pytest tests/unit/test_zoho_writer.py::test_update_zoho_payload -x` | ❌ Wave 0 |
| SYNC-6 | `write_todoist_id_to_zoho` uses resolved field_api_name | unit | `pytest tests/unit/test_zoho_writer.py::test_write_todoist_id -x` | ❌ Wave 0 |
| EDGE-3 | `update_todoist_task` uses `due_string="no date"` when due_date is None | unit | `pytest tests/unit/test_todoist_writer.py::test_update_clears_due_date -x` | ❌ Wave 0 |
| EDGE-3 | `update_zoho_task` sends `Due_Date: null` when due_date is None | unit | `pytest tests/unit/test_zoho_writer.py::test_update_clears_due_date -x` | ❌ Wave 0 |
| EDGE-4 | `complete_zoho_task` uses `ZOHO_TERMINAL_STATUSES` not hardcoded string | unit | `pytest tests/unit/test_zoho_writer.py::test_complete_uses_terminal_status -x` | ❌ Wave 0 |
| EDGE-1/2 | Deletion functions send Resend email after delete succeeds | unit | `pytest tests/unit/test_todoist_writer.py::test_delete_sends_email -x` | ❌ Wave 0 |
| EDGE-6 | Resend failure does not raise (logs only) | unit | `pytest tests/unit/test_todoist_writer.py::test_resend_failure_no_rollback -x` | ❌ Wave 0 |
| All | Idempotent: second call with same args does not duplicate | unit | `pytest tests/unit/test_todoist_writer.py -k idempotent -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/unit/test_todoist_writer.py tests/unit/test_zoho_writer.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/test_todoist_writer.py` — covers SYNC-1/2, EDGE-3 (due clear), EDGE-1/6 (delete+email)
- [ ] `tests/unit/test_zoho_writer.py` — covers SYNC-3/6, EDGE-3 (null Due_Date), EDGE-2/4 (complete/delete)

*(No new framework config needed — `pyproject.toml` asyncio_mode already set; `httpx_mock` fixture from `pytest-httpx` already installed)*

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — |
| V3 Session Management | no | — |
| V4 Access Control | no | — |
| V5 Input Validation | yes | `normalise_title()` / `normalise_due_date()` — already in Phase 1; writer receives pre-normalised `NormalisedTask` objects |
| V6 Cryptography | no | — |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed task title injection into Zoho Subject | Tampering | `normalise_title()` strips control characters; already applied before writer receives data |
| Sensitive data in Resend email body | Information Disclosure | Email body should reference task title and IDs only; never include OAuth tokens or API keys |
| Zoho access token leaked in log | Information Disclosure | Writers receive `access_token` as a parameter; never log the token value (established pattern from Phase 2) |

---

## Sources

### Primary (HIGH confidence)
- `/data/home/.local/lib/python3.11/site-packages/todoist_api_python/api_async.py` — method signatures for `add_task`, `update_task`, `complete_task`, `delete_task` (installed source, verified)
- `/data/home/.local/lib/python3.11/site-packages/todoist_api_python/_core/utils.py` — `kwargs_without_none` implementation confirming due_date=None drop behaviour (installed source, verified)
- `https://www.zoho.com/crm/developer/docs/api/v8/update-records.html` — PUT endpoint format and JSON body structure (fetched via WebFetch)
- `resend 2.29.0` — `resend.Emails.send_async()` async method (installed package confirmed)

### Secondary (MEDIUM confidence)
- `https://developer.todoist.com/api/v1/` — REST API `POST /tasks/{id}` update endpoint; `POST /tasks/{id}/close`; `DELETE /tasks/{id}` (fetched via WebFetch + Context7 docs)
- `https://doist.github.io/todoist-api-python/api_async` — async method signatures cross-referenced with installed source (Context7 via Bash CLI)

### Tertiary (LOW confidence, see Assumptions Log)
- Zoho `{"Due_Date": null}` clearing behaviour — inferred from JSON standard + Zoho null semantics; not confirmed against live Zoho API (A1)
- `due_string="no date"` Todoist due-date clearing — inferred from Todoist natural language date parsing conventions; not tested against live Todoist API (A2)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all three libraries are installed and inspected from source
- Architecture: HIGH — follows exact patterns from Phases 2/3; no new structural decisions
- Pitfalls: HIGH — `kwargs_without_none` behaviour verified from installed source code; Zoho 207 from official docs
- Due-date clearing: MEDIUM — `due_string="no date"` is inferred from Todoist docs; marked A2

**Research date:** 2026-04-24
**Valid until:** 2026-05-24 (stable API; Zoho v8 and Todoist v1 are not fast-moving)
