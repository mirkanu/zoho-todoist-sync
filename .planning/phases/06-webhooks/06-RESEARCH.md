# Phase 6: Webhooks - Research

**Researched:** 2026-04-24
**Domain:** FastAPI routing, HMAC-SHA256 webhook verification, raw request body access, arq job enqueue from HTTP handlers
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SYNC-4 | Zoho webhook is notification-only — `module` + `ids` fields only; worker fetches full task from API. | Confirmed in REQUIREMENTS.md; handler must extract `ids[0]` from payload and call `enqueue_sync`. |
| SYNC-8 | Todoist `item:added` without `[zoho:ID]` footer is discarded (logged, not enqueued). | `extract_zoho_id()` already in `app/todoist/normalise.py`; handler calls it on `event_data.description`. |
| INFRA-1 | Two Railway services: `web` (FastAPI) and `worker` (arq). Webhook handler lives in `web`. | Confirmed; `web` calls `enqueue_sync(redis, ...)` over Redis to hand off to `worker`. |
| INFRA-4 | FastAPI 0.136.0, arq 0.28.0 pinned. | Confirmed via `pip show`; no new dependencies needed. |
| LOOP-5 | Bootstrap race: `item:added` for service-created tasks has footer; handler must detect footer before discarding. | `extract_zoho_id` returns zoho_task_id → treat as sync-managed, NOT a new Todoist-native task. |
| EDGE-7 | Todoist `item:completed` fires webhook; handler must enqueue `sync_task` for the corresponding Zoho task. | Todoist event carries `event_data.id` (Todoist task ID); handler must look up `sync_state` to get zoho_task_id. |
| EDGE-8 | Missing footer on a task with a `sync_state` row: log WARN, defer to reconciler. | Handler discards the event after logging; reconciler (Phase 7) re-attaches footer. |

</phase_requirements>

---

## Summary

Phase 6 adds two webhook endpoints to the existing FastAPI `app`:

1. `POST /webhooks/zoho` — receives Zoho notification-only payloads, validates presence of `module` + `ids`, extracts the first task ID, and enqueues `sync_task` with a 2-second defer.
2. `POST /webhooks/todoist` — verifies the HMAC-SHA256 signature in `X-Todoist-Hmac-SHA256` against the **raw request body** using `TODOIST_CLIENT_SECRET`, then dispatches based on `event_name`.

The critical constraint for both endpoints: return HTTP 200 before any database or external API I/O. The only synchronous work inside these handlers is payload parsing and HMAC verification. All real work is handed to the arq worker via `enqueue_sync`.

The `ArqRedis` pool must be created once at app startup and stored in `app.state.redis`. Handlers access it via `request.app.state.redis`. This is the same pattern already used for `app.state.todoist_client` and `app.state.zoho_refresh_task` in `app/main.py`.

HMAC verification for Todoist webhooks MUST use the **raw request bytes** (`await request.body()`) before any JSON parsing. Parsing the body first, then re-serialising, will break the signature because JSON key ordering or whitespace may differ.

For the Todoist side, `event_data.id` is the Todoist task ID. The handler must look up `sync_state` by `todoist_task_id` to retrieve `zoho_task_id` before enqueueing. This is the only DB read permitted in the handler — it is a fast indexed PK lookup, not a write or external API call.

**Primary recommendation:** One new module `app/webhooks/` with `router.py` and `__init__.py`. Mount with `app.include_router(router, prefix="/webhooks")`. Extend `lifespan` in `app/main.py` to create and store the ArqRedis pool at startup.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Webhook payload validation (Zoho) | API / Backend (FastAPI handler) | — | Stateless; pure payload parsing; no external I/O |
| HMAC-SHA256 signature verification (Todoist) | API / Backend (FastAPI handler) | — | Must use raw bytes; must complete before any other processing |
| Job enqueue | Redis / arq | — | `enqueue_sync(redis, zoho_task_id)` forwards to worker over Redis |
| ArqRedis pool lifecycle | API / Backend (lifespan) | Redis | Opened at startup, closed at shutdown; stored in `app.state.redis` |
| `sync_state` lookup for Todoist events | Database / Storage | API handler | Indexed query on `todoist_task_id`; only read permitted in handler |
| Full sync logic | Worker (arq `sync_task`) | — | Handler NEVER executes sync logic; only enqueues |
| Footer extraction (LOOP-5) | API / Backend (handler) | — | `extract_zoho_id()` already implemented; handler calls it |
| Item deletion propagation | Worker (Phase 7 scope) | API handler (enqueue) | Handler enqueues delete path; worker handles propagation |

---

## Standard Stack

### Core (all already pinned)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.136.0 | HTTP framework; `APIRouter`, `Request`, `HTTPException` | Already in use in `app/main.py` |
| arq | 0.28.0 | `create_pool`, `ArqRedis` for Redis job enqueue | Already used by worker; `create_pool` needed in web lifespan |
| hashlib + hmac | stdlib | HMAC-SHA256 verification | stdlib; no extra dependency |
| SQLAlchemy[asyncio] | 2.0.49 | `sync_state` lookup for Todoist event routing | Already in use |

**No new dependencies required for Phase 6.**

### Key APIs to Use

| API | Module | Purpose |
|-----|--------|---------|
| `Request` | `fastapi` | Raw body access: `await request.body()` |
| `HTTPException` | `fastapi` | Return 400/401 from handler |
| `APIRouter` | `fastapi` | Module-level router; mounted at `/webhooks` |
| `create_pool` | `arq.connections` | Create ArqRedis pool at lifespan startup |
| `RedisSettings` | `arq.connections` | Parse `REDIS_URL` → `RedisSettings.from_dsn(settings.redis_url)` |
| `enqueue_sync` | `app.worker.enqueue` | Existing helper; reused by both handlers |
| `extract_zoho_id` | `app.todoist.normalise` | Footer extraction for LOOP-5/SYNC-8 |
| `hmac.compare_digest` | stdlib `hmac` | Constant-time comparison for HMAC verification |

---

## Architecture Patterns

### System Architecture Diagram

```
POST /webhooks/zoho                     POST /webhooks/todoist
      │                                        │
      │ 1. Parse JSON body                     │ 1. await request.body() → raw bytes
      │ 2. Validate module + ids               │ 2. HMAC-SHA256(raw, client_secret) → base64
      │ 3. Extract ids[0] as zoho_task_id      │ 3. compare_digest vs X-Todoist-Hmac-SHA256
      │ 4. enqueue_sync(redis,                 │    → 401 on mismatch
      │      zoho_task_id,                     │ 4. Parse JSON body
      │      defer_secs=settings.             │ 5. Branch on event_name:
      │        zoho_job_defer_secs)            │    item:added → extract_zoho_id check
      │ 5. return {"ok": True}                 │    item:updated/completed/uncompleted
      │                                        │      → lookup sync_state by todoist_task_id
      │                                        │      → enqueue_sync(redis, zoho_task_id, 0)
      │                                        │    item:deleted → enqueue delete path
      │                                        │    other → log + discard
      │                                        │ 6. return {"ok": True}
      │                                        │
      ▼                                        ▼
           Redis (arq queue)
                  │
                  ▼
           Worker: sync_task(ctx, zoho_task_id)
```

### Recommended Project Structure

```
app/
├── main.py              # extend lifespan: create_pool → app.state.redis
├── webhooks/
│   ├── __init__.py      # empty
│   └── router.py        # APIRouter; zoho + todoist endpoints
├── worker/
│   ├── enqueue.py       # existing enqueue_sync helper (reused as-is)
│   └── ...
tests/
└── unit/
    └── test_webhooks.py  # new test file
```

### Pattern 1: Raw Body HMAC Verification (Todoist)

CRITICAL: `request.body()` must be called BEFORE any JSON parsing. FastAPI caches the body bytes on the Request object after the first call, so subsequent `request.json()` still works. [VERIFIED: FastAPI 0.136.0 / Starlette Request implementation]

```python
# Source: FastAPI Request.body() + stdlib hmac
import base64
import hashlib
import hmac

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

@router.post("/todoist")
async def todoist_webhook(request: Request):
    raw_body = await request.body()            # raw bytes, before any JSON parse

    # HMAC verification
    settings = get_settings()
    expected = base64.b64encode(
        hmac.new(
            settings.todoist_client_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    received = request.headers.get("X-Todoist-Hmac-SHA256", "")
    if not hmac.compare_digest(expected, received):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()  # safe: body already cached
    ...
    return {"ok": True}
```

### Pattern 2: Zoho Notification-Only Handler

```python
# Source: REQUIREMENTS.md SYNC-4; confirmed Zoho webhook payload structure
@router.post("/zoho")
async def zoho_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    module = payload.get("module")
    ids = payload.get("ids")
    if not module or not ids or not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="missing module or ids")

    zoho_task_id = str(ids[0])
    settings = get_settings()
    redis = request.app.state.redis
    await enqueue_sync(redis, zoho_task_id, defer_secs=settings.zoho_job_defer_secs)
    return {"ok": True}
```

### Pattern 3: ArqRedis Pool in lifespan

The `web` service needs its own ArqRedis pool to enqueue jobs. This pool is created during lifespan startup and stored in `app.state.redis`. [VERIFIED: arq 0.28.0 `create_pool` API + project's existing `app.state` pattern]

```python
# In app/main.py lifespan, alongside existing startup code:
from arq.connections import create_pool, RedisSettings

redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
app.state.redis = redis
# ... at yield + shutdown:
await app.state.redis.aclose()
```

### Pattern 4: Todoist Event Routing

Todoist webhook `event_data.id` is the **Todoist task ID** (not Zoho). The handler must map it to `zoho_task_id` via `sync_state`. For `item:added`, check the footer first — if footer present, it is a sync-managed task (LOOP-5); if absent, discard (SYNC-8).

```python
# Todoist event dispatch (pseudocode)
event_name = payload.get("event_name", "")
event_data = payload.get("event_data", {})
todoist_task_id = str(event_data.get("id", ""))

if event_name == "item:added":
    description = event_data.get("description", "")
    zoho_id = extract_zoho_id(description)
    if zoho_id is None:
        log.info("todoist_item_added_no_footer_discarded", todoist_task_id=todoist_task_id)
        return {"ok": True}
    # footer present → sync-managed task → fall through to enqueue
    await enqueue_sync(redis, zoho_id, defer_secs=0)

elif event_name in ("item:updated", "item:completed", "item:uncompleted"):
    zoho_id = await _lookup_zoho_id(session_factory, todoist_task_id)
    if zoho_id is None:
        log.warning("todoist_event_no_sync_state", event_name=event_name, todoist_task_id=todoist_task_id)
        return {"ok": True}
    await enqueue_sync(redis, zoho_id, defer_secs=0)

elif event_name == "item:deleted":
    zoho_id = await _lookup_zoho_id(session_factory, todoist_task_id)
    if zoho_id:
        await enqueue_sync(redis, zoho_id, defer_secs=0)  # worker handles delete path

else:
    log.debug("todoist_event_ignored", event_name=event_name)

return {"ok": True}
```

### Pattern 5: sync_state Lookup by Todoist ID

The handler needs a single indexed read of `sync_state` to map `todoist_task_id → zoho_task_id`. This is the ONLY DB operation permitted in the handler.

```python
# Source: existing app/db/models.py SyncState model + SQLAlchemy async pattern
from sqlalchemy import select
from app.db.models import SyncState

async def _lookup_zoho_id(session_factory, todoist_task_id: str) -> str | None:
    async with session_factory() as session:
        result = await session.execute(
            select(SyncState.zoho_task_id).where(
                SyncState.todoist_task_id == todoist_task_id
            )
        )
        row = result.scalar_one_or_none()
    return row
```

The `session_factory` is accessed via `request.app.state.session_factory` (or passed into a FastAPI Depends). The same `session_factory` already stored in `app.state` during lifespan.

### Anti-Patterns to Avoid

- **Reading raw body AFTER json():** FastAPI will have consumed the body stream; `request.body()` returns empty bytes. Always call `request.body()` first.
- **Using `==` for HMAC comparison:** Timing-attack vulnerable. Always use `hmac.compare_digest()`.
- **Parsing the body, re-serialising for HMAC:** JSON serialisation is not deterministic; re-serialised bytes will NOT match the original. Use the original raw bytes.
- **Performing any write or external API call inside the webhook handler:** Return 200 immediately after enqueueing. Writes belong in the worker.
- **Creating a new ArqRedis connection per request:** Always reuse the pool from `app.state.redis`. Per-request connections are slow and will exhaust Redis connection limits.
- **Storing session_factory from scratch in handler:** The lifespan already builds the session_factory; store it in `app.state.session_factory` and reuse.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Constant-time comparison | Custom string equality | `hmac.compare_digest()` | Prevents timing attacks; stdlib; one line |
| HMAC computation | Custom hash | `hmac.new(..., hashlib.sha256).digest()` + `base64.b64encode` | stdlib; matches Todoist's exact algorithm |
| ArqRedis pool | Custom Redis client | `arq.connections.create_pool(RedisSettings.from_dsn(...))` | Already used by worker; same pool type; supports `enqueue_job` |
| Job enqueue | Custom Redis commands | `app.worker.enqueue.enqueue_sync` | Already implemented in Phase 5; handles dedup + logging |
| Footer extraction | Custom regex | `extract_zoho_id()` from `app.todoist.normalise` | Already implemented in Phase 3; handles all edge cases |

**Key insight:** Every non-trivial piece needed by these handlers already exists in the codebase. Phase 6 is pure assembly work.

---

## Todoist Webhook Payload Structure

[MEDIUM: verified via community sources + official header documentation; event schema confirmed across multiple real implementations]

### Top-Level Fields

```json
{
  "event_name": "item:updated",
  "user_id": 12345,
  "initiator": {
    "id": 12345,
    "full_name": "Manuel Kuhs",
    "email": "manuelkuhs@gmail.com",
    "is_premium": true,
    "image_id": null
  },
  "event_data": {
    "id": "7890123",
    "content": "Task title",
    "description": "\n\n---\n[zoho:4567890]",
    "project_id": "6gCPcWwM392GhXQh",
    "checked": 0,
    "is_deleted": false,
    "priority": 2,
    "due": {"date": "2026-05-01"},
    "labels": []
  }
}
```

### Event Names Used in Phase 6

| Event | Trigger | Handler Action |
|-------|---------|----------------|
| `item:added` | New task created | Check footer; discard if absent; enqueue if present (LOOP-5) |
| `item:updated` | Task edited | Lookup `sync_state`; enqueue `sync_task` |
| `item:completed` | Task marked done | Lookup `sync_state`; enqueue `sync_task` |
| `item:uncompleted` | Task re-opened | Lookup `sync_state`; enqueue `sync_task` |
| `item:deleted` | Task deleted | Lookup `sync_state`; enqueue delete path |
| anything else | — | Log debug; return 200 |

### HMAC Verification Header

- Header name: `X-Todoist-Hmac-SHA256`
- Algorithm: HMAC-SHA256 over raw UTF-8 request body, key = `TODOIST_CLIENT_SECRET`, output = base64-encoded digest
- Mismatch: return HTTP 401 immediately, do NOT log the body

### Key `event_data` Fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Todoist task ID — use to look up `sync_state.todoist_task_id` |
| `content` | string | Task title |
| `description` | string | Contains `[zoho:ID]` footer; may be null |
| `checked` | int | `1` = completed (older Sync API); or use `event_name` directly |
| `is_deleted` | bool | `true` for `item:deleted` events |
| `project_id` | string | Filter check if needed (project isolation) |

---

## Zoho Webhook Payload Structure

[VERIFIED: REQUIREMENTS.md SYNC-4, confirmed via Phase 2 research]

```json
{
  "module": "Tasks",
  "ids": ["4567890123456789"]
}
```

- `module`: always present; value is the Zoho module name (e.g., `"Tasks"`)
- `ids`: array of record IDs that changed; take `ids[0]` as the Zoho task ID
- No field values in the payload — worker MUST fetch from Zoho API on dequeue

Validation: HTTP 400 if `module` or `ids` missing, or `ids` is empty.

---

## Common Pitfalls

### Pitfall 1: HMAC on Parsed Body (Not Raw Bytes)
**What goes wrong:** Compute HMAC on `json.dumps(await request.json())` — the result will not match `X-Todoist-Hmac-SHA256` because JSON re-serialisation changes whitespace, key order, or encoding.
**Why it happens:** FastAPI JSON auto-parsing feels natural; developers forget the HMAC needs the exact wire bytes.
**How to avoid:** Always call `raw = await request.body()` first, compute HMAC on `raw`, THEN parse JSON.
**Warning signs:** All Todoist webhooks return 401 in testing even with the correct secret.

### Pitfall 2: No ArqRedis Pool in Web Service
**What goes wrong:** Webhook handler has no Redis client; cannot enqueue jobs; returns 500.
**Why it happens:** The `worker` process has a Redis connection, but the `web` process does not automatically.
**How to avoid:** In `lifespan`, create an ArqRedis pool via `create_pool(RedisSettings.from_dsn(settings.redis_url))` and store in `app.state.redis`.
**Warning signs:** `AttributeError: 'State' object has no attribute 'redis'` at request time.

### Pitfall 3: Sync DB Write Inside Handler
**What goes wrong:** Handler writes to `sync_events` or updates `sync_state` — handler latency exceeds 200ms; webhook delivery timeouts from Todoist/Zoho cause duplicate deliveries.
**Why it happens:** Developers want to log immediately; forget the 200ms SLA.
**How to avoid:** Worker handles all writes. Handler only reads (one indexed lookup) and enqueues.
**Warning signs:** Response times > 100ms under load.

### Pitfall 4: `item:added` for Service-Created Tasks Enqueuing Reverse Sync
**What goes wrong:** When the sync service creates a Todoist task, Todoist fires `item:added`. Handler treats it as a new Todoist-native task and tries to create a Zoho task — infinite loop.
**Why it happens:** LOOP-5 mitigation not implemented; footer check omitted.
**How to avoid:** Always call `extract_zoho_id(event_data.get("description"))` for `item:added`. If footer present → sync-managed; if absent → discard.
**Warning signs:** New Todoist tasks created in pairs; `sync_events` shows rapid `zoho_to_todoist` entries for the same task.

### Pitfall 5: Timing Attack on HMAC Comparison
**What goes wrong:** Using `if expected == received:` — timing-vulnerable; can leak key via response time.
**Why it happens:** String equality feels natural.
**How to avoid:** Always use `hmac.compare_digest(expected, received)`. Both must be the same type (str or bytes).
**Warning signs:** Linter or security scanner flags string equality on HMAC output.

### Pitfall 6: Todoist Event With No sync_state Row
**What goes wrong:** Handler receives `item:updated` for a Todoist task that was never synced (e.g., a task in another project, or a task pre-migration). Handler tries to enqueue with `None` as `zoho_task_id`.
**Why it happens:** Todoist webhooks fire for ALL tasks in the account, not just the synced project.
**How to avoid:** Null-check the result of `_lookup_zoho_id`; if None, log and return 200. Consider filtering by `event_data.project_id == settings.todoist_project_id` as a first gate.
**Warning signs:** `sync_task` job fails with "Zoho task not found" for tasks that were never in Zoho.

### Pitfall 7: Missing `aclose()` on ArqRedis at Shutdown
**What goes wrong:** ArqRedis pool leaks at graceful shutdown; Railway restart logs Redis connection errors.
**Why it happens:** Lifespan shutdown block forgotten.
**How to avoid:** In lifespan shutdown: `await app.state.redis.aclose()`.

---

## Code Examples

### Full HMAC Verification Function

```python
# Source: stdlib hmac + hashlib; Todoist official header name confirmed
import base64
import hashlib
import hmac

def verify_todoist_hmac(raw_body: bytes, client_secret: str, received_header: str) -> bool:
    """Return True iff the HMAC-SHA256 of raw_body under client_secret
    matches received_header (base64-encoded).
    Uses hmac.compare_digest to prevent timing attacks.
    """
    digest = hmac.new(
        client_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, received_header)
```

### Router Mount in main.py

```python
# Source: FastAPI include_router pattern [VERIFIED: fastapi/fastapi docs]
from app.webhooks.router import router as webhooks_router

app = FastAPI(lifespan=lifespan)
app.include_router(webhooks_router, prefix="/webhooks", tags=["webhooks"])
```

### ArqRedis Lifecycle in lifespan

```python
# Source: arq 0.28.0 create_pool [VERIFIED: arq source + existing WorkerSettings pattern]
from arq.connections import create_pool, RedisSettings

async with lifespan:
    ...
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.redis = redis
    app.state.session_factory = session_factory  # reuse existing for Todoist handler

    yield

    await app.state.redis.aclose()
```

---

## State of the Art

| Old Approach | Current Approach | Notes |
|--------------|------------------|-------|
| Sync Flask + Redis queue | Async FastAPI + arq | Already in use in this project |
| `BackgroundTasks.add_task()` for side effects | `enqueue_sync()` → Redis → dedicated worker | Correct for crash-safe enqueue; `BackgroundTasks` is in-process, lost on crash |
| Re-parse body for HMAC | Use raw bytes from `request.body()` | FastAPI caches body; safe to call `request.body()` then `request.json()` |

**Not applicable:**

- `BackgroundTasks` is explicitly NOT used for enqueueing because it runs in-process and is lost if the web process crashes before it runs. The Redis-backed arq queue survives process restarts.

---

## Environment Availability

Step 2.6: SKIPPED (no new external tools required — all dependencies already available in the environment).

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `python3 -m pytest tests/unit/test_webhooks.py -x -q` |
| Full suite command | `python3 -m pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SYNC-4 | Zoho handler extracts `ids[0]`, enqueues with defer | unit | `pytest tests/unit/test_webhooks.py::test_zoho_webhook_enqueues -x` | ❌ Wave 0 |
| SYNC-8 | Todoist `item:added` without footer is discarded | unit | `pytest tests/unit/test_webhooks.py::test_todoist_item_added_no_footer_discarded -x` | ❌ Wave 0 |
| INFRA-4 | Router registered at `/webhooks/zoho` and `/webhooks/todoist` | unit | `pytest tests/unit/test_webhooks.py::test_router_paths -x` | ❌ Wave 0 |
| LOOP-5 | `item:added` with footer → enqueues, not discarded | unit | `pytest tests/unit/test_webhooks.py::test_todoist_item_added_with_footer_enqueues -x` | ❌ Wave 0 |
| EDGE-7 | `item:completed` → lookup sync_state → enqueue | unit | `pytest tests/unit/test_webhooks.py::test_todoist_item_completed_enqueues -x` | ❌ Wave 0 |
| EDGE-8 | Missing footer on sync_state task → log WARN, return 200 | unit | `pytest tests/unit/test_webhooks.py::test_todoist_missing_footer_on_synced_task -x` | ❌ Wave 0 |
| INFRA-1 | HMAC mismatch → 401 | unit | `pytest tests/unit/test_webhooks.py::test_todoist_invalid_hmac_returns_401 -x` | ❌ Wave 0 |
| SYNC-4 | Zoho handler: missing `module` → 400 | unit | `pytest tests/unit/test_webhooks.py::test_zoho_missing_module_returns_400 -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `python3 -m pytest tests/unit/test_webhooks.py -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_webhooks.py` — all webhook handler tests (listed above)
- [ ] `app/webhooks/__init__.py` — package marker
- [ ] `app/webhooks/router.py` — webhook router module

*(No test infrastructure gaps — `conftest.py` and pytest-asyncio already configured)*

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — |
| V3 Session Management | no | — |
| V4 Access Control | no | — |
| V5 Input Validation | yes | Pydantic payload model or manual field checks |
| V6 Cryptography | yes | `hmac.compare_digest` + `hmac.new` + `hashlib.sha256` — never hand-roll |

### Known Threat Patterns for Webhook Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Spoofed webhook from non-Todoist origin | Spoofing | HMAC-SHA256 verification on all Todoist webhooks |
| Replay attack (re-send valid webhook) | Repudiation | arq `_job_id` dedup drops duplicate job; idempotent `sync_task` |
| Timing attack on HMAC comparison | Information disclosure | `hmac.compare_digest()` |
| Zoho payload injection (crafted `ids`) | Tampering | Validate `ids` is non-empty list; worker fetches from Zoho API, not from payload |
| DoS via large payload | DoS | FastAPI default request size limits apply; no action needed |

---

## Open Questions

1. **Zoho webhook payload: is `ids` always a list, or sometimes a bare string?**
   - What we know: REQUIREMENTS.md says "payload contains `module` + `ids` only"
   - What's unclear: Whether Zoho delivers `ids` as `["12345"]` or `"12345"` in some webhook configurations
   - Recommendation: Accept both list and string in the handler; normalise to string via `str(ids[0] if isinstance(ids, list) else ids)`

2. **Todoist event filter by project_id: needed in handler or rely on Phase 7 reconciler?**
   - What we know: Todoist webhooks fire for ALL events in the account
   - What's unclear: Whether the webhook subscription is already scoped to the target project
   - Recommendation: Add a project_id gate in the Todoist handler (`event_data.get("project_id") == settings.todoist_project_id`); log and discard mismatches to reduce spurious lookups

3. **`session_factory` access in webhook handler: `app.state` or Depends?**
   - What we know: `app.state` already stores `todoist_client`; `session_factory` is built in lifespan
   - What's unclear: Whether `session_factory` is currently stored in `app.state` (it is NOT in the current `main.py`)
   - Recommendation: Store `session_factory` in `app.state` during lifespan startup (one-line addition); access as `request.app.state.session_factory` in handler

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Todoist `event_data.description` contains the `[zoho:ID]` footer | Todoist Payload Structure | Handler would fail to detect LOOP-5/SYNC-8; may need to use `event_data.content` instead — verify against live Todoist webhook delivery |
| A2 | Todoist `event_data.id` is a string (not integer) | Todoist Payload Structure | `_lookup_zoho_id` call would need `str()` cast — low impact |
| A3 | Zoho `ids` field is always a JSON array | Zoho Payload Structure | Handler would 400 on bare-string delivery from some Zoho webhook configurations |

---

## Sources

### Primary (HIGH confidence)
- FastAPI `/fastapi/fastapi` Context7 — `Request.body()`, `APIRouter`, `include_router`, `HTTPException` patterns
- arq `/websites/arq-docs_helpmanual_io` Context7 — `create_pool`, `ArqRedis`, `enqueue_job` API
- Project source (`app/main.py`, `app/worker/enqueue.py`, `app/todoist/normalise.py`) — existing patterns for lifespan, `app.state`, `enqueue_sync`, `extract_zoho_id`
- `requirements.txt` + `pip show` — confirmed all versions

### Secondary (MEDIUM confidence)
- Todoist webhook header `X-Todoist-Hmac-SHA256` and HMAC-SHA256 algorithm — confirmed across rollout.com, BenMatheja/todoist-serverless-lambda, and Todoist developer docs navigation
- Todoist event names (`item:added`, `item:updated`, `item:completed`, `item:uncompleted`, `item:deleted`) — confirmed in multiple community implementations and official nav structure
- Todoist `event_data` field names (`id`, `content`, `description`, `checked`, `is_deleted`) — confirmed via BenMatheja/todoist-serverless-lambda payload example

### Tertiary (LOW confidence)
- Todoist `event_data.description` as the field containing the footer (vs. `event_data.content`) — inferred from Todoist data model; not confirmed in live webhook sample

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in use; versions confirmed
- Architecture: HIGH — patterns derived from existing `app/main.py` + confirmed FastAPI/arq docs
- Pitfalls: HIGH — HMAC raw-body pitfall confirmed by multiple real-world implementations
- Todoist payload schema: MEDIUM — field names confirmed in community sources; `description` vs `content` for footer is LOW (A1)

**Research date:** 2026-04-24
**Valid until:** 2026-05-24 (stable APIs; 30-day window)
