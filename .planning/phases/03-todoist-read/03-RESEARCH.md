# Phase 3: Todoist Read - Research

**Researched:** 2026-04-24
**Domain:** Todoist REST API v1, Todoist Sync API v9, `todoist-api-python` 4.0.0 async client, sync_token persistence, footer regex parsing
**Confidence:** HIGH (SDK verified from installed source; Sync API endpoint/format verified from official docs)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SYNC-5 | Footer format `\n\n---\n[zoho:{ZOHO_TASK_ID}]`; regex `\[zoho:(\d+)\]`; must survive user edits above the footer | `ZOHO_ID_RE` already defined in `app/core/normalise.py` and exported; unit tests must cover mid-text, missing, and post-edit cases |
| SYNC-8 | `item:added` events without footer are logged and discarded; Sync API delta items without footer are ignored | Footer extraction function gates all Sync API item processing; REST fetch path also checks footer before storing |
| SYNC-9 | Todoist labels never propagated to Zoho; excluded from canonical hash | Labels field read from `Task.labels`; never passed to normalisation or hash; confirmed canonical hash already excludes labels |
| SEED-7 | `sync_token="*"` on startup for full snapshot; returned `sync_token` persisted to Postgres `kv_store`; on restart load stored token and resume incrementally | Sync API endpoint verified (`POST https://api.todoist.com/api/v1/sync`); `kv_store` table exists; `upsert_kv` pattern from Phase 2 reused |

</phase_requirements>

---

## Summary

Phase 3 builds the Todoist read layer: a `TodoistClient` that wraps `TodoistAPIAsync` (from the installed `todoist-api-python==4.0.0` SDK), a `sync_token` poll loop using the Todoist Sync API v9 directly via `httpx`, and the `extract_zoho_id()` footer parser.

The key architectural finding is that **`todoist-api-python` 4.0.0 ships a native async class `TodoistAPIAsync`** using `httpx.AsyncClient` internally. This is directly usable in the async stack — no thread bridging required. `TodoistAPIAsync.get_task(task_id)` returns a fully-typed `Task` dataclass with `id`, `content`, `description`, `priority`, `due`, `labels`, and `is_completed` (property). This covers the REST path (`fetch_todoist_task`).

The Sync API (`sync_token` incremental poll) is a **separate endpoint** — `POST https://api.todoist.com/api/v1/sync` — that the SDK does not expose. It must be called directly via `httpx.AsyncClient` with `sync_token` and `resource_types=["items"]` as form parameters. The response returns an `items` array and a new `sync_token`.

The `extract_zoho_id()` function is largely already implemented: `ZOHO_ID_RE = re.compile(r"\[zoho:(\d+)\]")` is already exported from `app/core/normalise.py`. Phase 3 wraps it in a standalone function for clarity.

**Primary recommendation:** Use `TodoistAPIAsync` from the SDK for single-task REST fetches. Call the Sync API directly via `httpx.AsyncClient` for `sync_token` polling. Reuse `upsert_kv` from Phase 2 to persist the `sync_token`. The `ZOHO_ID_RE` regex is already correct and tested.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| `fetch_todoist_task(id)` | API / Backend (worker) | — | Called by `sync_task` job on dequeue to read live Todoist state |
| Todoist Sync API poll (`sync_token`) | API / Backend (worker/reconciler) | — | Called during startup (full sync) and by reconciliation cron (incremental) |
| `sync_token` persistence | Database / Storage | — | `kv_store` table; same `upsert_kv` pattern as Zoho token |
| `extract_zoho_id(description)` | API / Backend (all) | — | Pure function; called by webhook handler, Sync API processor, and REST fetch path |
| Project filtering (Sync API items) | API / Backend | — | Client-side only — Sync API returns all items; filter by `project_id == TODOIST_PROJECT_ID` |
| Auth failure handling | API / Backend | — | 401 from REST API raises `TodoistAuthError`; stops sync and alerts |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `todoist-api-python` | 4.0.0 (installed) | Async REST client for single-task fetch, task CRUD (future phases) | Already installed; ships `TodoistAPIAsync` with `httpx.AsyncClient` internally; fully typed `Task` dataclass |
| `httpx` | 0.28.1 (installed) | Direct Sync API calls (`POST /api/v1/sync`) | SDK does not expose Sync API; httpx already used for Zoho client — consistent |
| `re` (stdlib) | — | `extract_zoho_id()` regex parsing | `ZOHO_ID_RE` already defined in `app/core/normalise.py` |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `asyncio` | stdlib | Background sync_token refresh / startup poll | Same pattern as Zoho proactive_refresh_loop |
| SQLAlchemy async + asyncpg | 2.0.49 / 0.31.0 | Persist/load `sync_token` from `kv_store` | Already available; `upsert_kv` from Phase 2 reused |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `TodoistAPIAsync` for REST | Raw `httpx` calls | SDK provides typed `Task` dataclass and correct URL construction; consistent with project's installed package |
| Direct `httpx` for Sync API | Third-party Sync API wrapper | No maintained async wrapper for Sync API exists; direct httpx is correct |
| New `extract_zoho_id()` impl | Reuse `ZOHO_ID_RE` from `app/core/normalise.py` | Regex is already correct and exported; wrap it in a thin function instead of duplicating |

**Installation:**
```bash
# Nothing new to install — all dependencies are already in requirements.txt
```

**Version verification:** [VERIFIED: installed package at /data/home/.local/lib/python3.11/site-packages/todoist_api_python/] — `todoist-api-python==4.0.0`, `httpx==0.28.1`

---

## Architecture Patterns

### System Architecture Diagram

```
  FastAPI lifespan / arq worker startup
           │
           ▼
  ┌─────────────────────────────────────┐
  │ 1. Load sync_token from kv_store    │
  │    (key="todoist_sync_token")       │
  │    If missing → use "*" (full sync) │
  │ 2. POST /api/v1/sync                │
  │    sync_token="*" or stored token   │
  │    resource_types=["items"]         │
  │ 3. Receive items[] + new sync_token │
  │ 4. Filter items by project_id       │
  │    (client-side)                    │
  │ 5. Persist new sync_token to        │
  │    kv_store via upsert_kv           │
  └─────────────────────────────────────┘
           │
           │  items[] from Sync API delta
           ▼
  ┌─────────────────────────────────────┐
  │ For each item:                      │
  │   extract_zoho_id(description)      │
  │   → None: log + discard (SYNC-8)   │
  │   → zoho_id: pass to sync pipeline │
  └─────────────────────────────────────┘

  Separate path: worker job dequeue
           │
           ▼
  fetch_todoist_task(todoist_task_id)
           │
           │  TodoistAPIAsync.get_task()
           ▼
  Task dataclass (id, content, description,
                  priority, due, labels,
                  is_completed)
           │
           ▼
  todoist_task_to_normalised()
  → NormalisedTask (title, due_date,
                    priority, is_completed)
  [labels excluded — SYNC-9]
```

### Recommended Project Structure

```
app/
├── todoist/
│   ├── __init__.py          # empty, same pattern as app/zoho/__init__.py
│   ├── client.py            # TodoistClient: wrap TodoistAPIAsync + typed exceptions
│   │                        # + fetch_sync_delta() for Sync API
│   ├── normalise.py         # todoist_task_to_normalised() adapter
│   └── sync_manager.py      # startup_sync() + load/persist sync_token
tests/
└── unit/
    ├── test_todoist_client.py    # mock httpx responses; test error mapping
    └── test_todoist_normalise.py # test footer extraction + normalisation
```

### Pattern 1: TodoistClient wrapping TodoistAPIAsync

**What:** A thin `TodoistClient` class that holds a `TodoistAPIAsync` instance, wraps its error responses into typed exceptions matching the project convention, and provides `fetch_todoist_task()`.

**When to use:** Every REST API call to Todoist in the worker.

**Key insight:** `TodoistAPIAsync` raises `httpx.HTTPStatusError` on non-2xx responses. Map these to typed exceptions the same way `ZohoClient._handle()` does.

```python
# app/todoist/client.py
# Source: [VERIFIED: installed todoist_api_python/api_async.py]
import httpx
from todoist_api_python.api_async import TodoistAPIAsync
from todoist_api_python.models import Task

from app.core.logging import get_logger

log = get_logger(__name__)


class TodoistAuthError(Exception):
    """Raised on 401 — API token invalid. Do NOT retry — stop and alert."""


class TodoistNotFoundError(Exception):
    """Raised on 404 — task does not exist (may be deleted)."""


class TodoistRateLimitError(Exception):
    """Raised on 429 — rate limit exceeded. Retry with backoff."""


class TodoistAPIError(Exception):
    """Raised on other non-2xx responses."""


class TodoistClient:
    """
    Async Todoist REST client.
    Wraps TodoistAPIAsync and maps httpx.HTTPStatusError to typed exceptions.
    """

    def __init__(self, api_token: str) -> None:
        self._api = TodoistAPIAsync(token=api_token)

    async def close(self) -> None:
        await self._api.close()

    async def fetch_todoist_task(self, todoist_task_id: str) -> Task:
        """
        Fetch a single Todoist task by ID.
        Returns Task dataclass with: id, content, description, priority,
        due (Due|None), labels (list[str]|None), is_completed (bool property).
        Raises TodoistAuthError / TodoistNotFoundError / TodoistRateLimitError.
        """
        try:
            return await self._api.get_task(todoist_task_id)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise TodoistAuthError(f"401 Unauthorized — task {todoist_task_id}") from exc
            if status == 404:
                raise TodoistNotFoundError(f"404 Not Found — task {todoist_task_id}") from exc
            if status == 429:
                raise TodoistRateLimitError(f"429 Rate limit — task {todoist_task_id}") from exc
            raise TodoistAPIError(f"{status} — task {todoist_task_id}") from exc

    async def fetch_sync_delta(
        self, sync_token: str, project_id: str | None = None
    ) -> tuple[list[dict], str]:
        """
        Call the Todoist Sync API for incremental item updates.
        Returns (items_list, new_sync_token).
        items_list is filtered to project_id if provided (client-side).
        Raises TodoistAuthError on 401.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.todoist.com/api/v1/sync",
                headers={"Authorization": f"Bearer {self._api._token}"},
                data={
                    "sync_token": sync_token,
                    "resource_types": '["items"]',
                },
            )
        if resp.status_code == 401:
            raise TodoistAuthError("401 Unauthorized — Sync API")
        if resp.status_code == 429:
            raise TodoistRateLimitError("429 Rate limit — Sync API")
        resp.raise_for_status()
        body = resp.json()
        items = body.get("items", [])
        new_token = body["sync_token"]
        if project_id:
            items = [i for i in items if i.get("project_id") == project_id]
        return items, new_token
```

### Pattern 2: extract_zoho_id() using existing regex

**What:** A thin wrapper around `ZOHO_ID_RE` already defined in `app/core/normalise.py`. Returns the Zoho task ID as a string, or `None` if no footer present. Works regardless of footer position in the description.

**When to use:** Every time a Todoist description is processed — Sync API items, REST task fetch, webhook handler (Phase 6).

```python
# app/todoist/client.py or app/core/normalise.py (can stay in normalise.py)
# Source: [VERIFIED: app/core/normalise.py — ZOHO_ID_RE already exported]
from app.core.normalise import ZOHO_ID_RE


def extract_zoho_id(description: str | None) -> str | None:
    """
    Parse [zoho:(\d+)] from anywhere in the description.
    Returns the Zoho task ID as a string, or None if not present.
    Works if footer is at end, mid-text, or after user edits above it.
    """
    if not description:
        return None
    m = ZOHO_ID_RE.search(description)
    return m.group(1) if m else None
```

**Unit test cases required (SYNC-5):**

```python
assert extract_zoho_id(None) is None
assert extract_zoho_id("") is None
assert extract_zoho_id("Just a title") is None
assert extract_zoho_id("Title\n\n---\n[zoho:12345]") == "12345"
assert extract_zoho_id("[zoho:99] in the middle of text") == "99"
assert extract_zoho_id("User edited this\n\n---\n[zoho:12345]") == "12345"
assert extract_zoho_id("Text\n[zoho:abc]") is None  # non-digit ID
```

### Pattern 3: sync_token persistence (startup + update)

**What:** Load the `sync_token` from `kv_store` at startup. If missing or empty, use `"*"` for full sync. After each successful Sync API poll, persist the returned token with `upsert_kv`.

**Key:** `upsert_kv` is already implemented in `app/zoho/token_manager.py` and is importable. The KV key for the Todoist sync token is `"todoist_sync_token"`.

```python
# app/todoist/sync_manager.py
# Source: [VERIFIED: app/zoho/token_manager.py — upsert_kv pattern]
from app.zoho.token_manager import upsert_kv   # reuse from Phase 2

KV_SYNC_TOKEN_KEY = "todoist_sync_token"


async def load_sync_token(session) -> str:
    """Load stored sync_token from kv_store. Returns '*' if missing."""
    from app.db.models import KVStore
    from sqlalchemy import select
    result = await session.execute(
        select(KVStore).where(KVStore.key == KV_SYNC_TOKEN_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None or not row.value:
        return "*"
    return row.value


async def save_sync_token(session, token: str) -> None:
    """Persist sync_token to kv_store and commit."""
    await upsert_kv(session, KV_SYNC_TOKEN_KEY, token)
    await session.commit()
```

### Pattern 4: todoist_task_to_normalised() adapter

**What:** Convert a `Task` dataclass (from `TodoistAPIAsync`) to a `NormalisedTask`. Labels are explicitly excluded (SYNC-9). Due date is extracted from `task.due.date` — a `date | datetime` union from the SDK — and normalised to `YYYY-MM-DD` via the existing `normalise_due_date()`.

**Key insight:** `Task.due` is a `Due` dataclass with a `.date` attribute typed as `ApiDue` (union of `date | datetime`). For date-only tasks, `.date` is a `datetime.date`; for datetime tasks, it is a `datetime.datetime`. Converting either to `str()` then passing to `normalise_due_date()` works because `normalise_due_date()` calls `datetime.fromisoformat()` which handles both.

```python
# app/todoist/normalise.py
# Source: [VERIFIED: todoist_api_python/models.py — Task, Due dataclasses]
from todoist_api_python.models import Task
from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title
from app.core.priority import todoist_to_zoho_priority  # for reverse mapping in Phase 4


def todoist_task_to_normalised(task: Task) -> NormalisedTask:
    """
    Convert a Task dataclass to NormalisedTask.
    Labels are NEVER included — excluded per SYNC-9.
    due.date is a date|datetime union; normalise_due_date handles both.
    is_completed comes from task.completed_at is not None (SDK property).
    """
    raw_due = None
    if task.due is not None:
        raw_due = str(task.due.date)  # date or datetime → str → normalise
    return NormalisedTask(
        title=normalise_title(task.content),
        due_date=normalise_due_date(raw_due),
        priority=task.priority,        # already Todoist int 1–4
        is_completed=task.is_completed,  # True if completed_at is not None
    )
```

### Anti-Patterns to Avoid

- **Using `TodoistAPI` (sync) instead of `TodoistAPIAsync`:** The sync version uses `httpx.Client` (blocking). In an async stack this blocks the event loop. Always import from `todoist_api_python.api_async`.
- **Calling the Sync API via the SDK:** `TodoistAPIAsync` does not expose the Sync API. Always call `POST https://api.todoist.com/api/v1/sync` directly via `httpx.AsyncClient`.
- **Assuming Sync API filters by project server-side:** The Sync API returns all items for the authenticated user. Filtering by `project_id` must be done client-side on the returned `items` array.
- **Storing `sync_token` only in memory:** Like the Zoho token, the sync_token must be persisted to `kv_store` for restart recovery. In-memory-only means a full resync on every restart.
- **Not handling `is_deleted: true` in Sync API items:** Deleted tasks appear in the items array with `is_deleted=True`. These must be handled separately from updated tasks (Phase 4/5). In Phase 3, log and skip deleted items.
- **Re-raising `StopIteration` from SDK paginator in async context:** `TodoistAPIAsync.get_tasks()` returns an `AsyncIterator`; do not call it inside a sync generator.
- **Holding `TodoistAPIAsync` open forever without closing:** The SDK warns if not closed. Either use `async with TodoistAPIAsync(...)` or call `await api.close()` in lifespan shutdown.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| REST task fetch + typed response | Raw httpx + manual JSON parse | `TodoistAPIAsync.get_task()` | SDK returns typed `Task` dataclass with correct field aliases; handles `dataclass-wizard` deserialisation |
| Footer regex | Custom regex from scratch | `ZOHO_ID_RE` from `app/core/normalise.py` | Already implemented, exported, and consistent; no duplication |
| `sync_token` KV persistence | New DB helper | `upsert_kv` from `app/zoho/token_manager.py` | Already tested; identical pattern |
| Sync API response field extraction | Manual dict access | Read `body["sync_token"]` and `body.get("items", [])` | Sync API response is a flat dict; no special library needed |

**Key insight:** Most of Phase 3 is wiring already-existing pieces together, not writing new logic. The regex is in `normalise.py`, the KV upsert is in `token_manager.py`, and the async HTTP client is in `TodoistAPIAsync`.

---

## Common Pitfalls

### Pitfall 1: Sync API — project_id filtering is client-side only

**What goes wrong:** Developer assumes `resource_types=["items"]` returns only the items from the configured project. In reality, it returns ALL items for the authenticated user across all projects.

**Why it happens:** The Sync API does not accept a `project_id` filter parameter. It is a user-level snapshot, not project-scoped.

**How to avoid:** After receiving `items` from the Sync API response, filter by `item["project_id"] == settings.todoist_project_id` before processing.

**Warning signs:** Tasks from other Todoist projects appearing in sync logs.

### Pitfall 2: `TodoistAPI` (sync) vs `TodoistAPIAsync` (async)

**What goes wrong:** Code imports `TodoistAPI` (sync class) instead of `TodoistAPIAsync`. The sync class uses `httpx.Client` which blocks the asyncio event loop on every call.

**Why it happens:** Both classes are in the installed package. The sync class is the default in examples.

**How to avoid:** Always `from todoist_api_python.api_async import TodoistAPIAsync`. Add a lint check or comment at the import site.

**Warning signs:** arq worker stalls; asyncio event loop warnings; all jobs slow under load.

### Pitfall 3: `Task.due.date` is a `date | datetime` union, not a string

**What goes wrong:** Code calls `task.due.date.isoformat()` on what turns out to be a `datetime.date` (no `.isoformat()` in some Python versions returns a different format), or passes it directly to `normalise_due_date()` which expects a `str | None`.

**Why it happens:** The SDK uses `dataclass-wizard`'s `ApiDue` type which deserialises to either `datetime.date` or `datetime.datetime` depending on the API response format.

**How to avoid:** Convert via `str(task.due.date)` before passing to `normalise_due_date()`. The `str()` of a `datetime.date` produces `"YYYY-MM-DD"`; the `str()` of a `datetime.datetime` produces `"YYYY-MM-DD HH:MM:SS"` (without timezone), which `normalise_due_date()` handles via `datetime.fromisoformat()`.

**Warning signs:** `normalise_due_date()` returns `None` for tasks with a due date set.

### Pitfall 4: Sync API `is_deleted` items in the delta

**What goes wrong:** Code treats all items in the Sync API `items` array as updated tasks. Deleted tasks are included with `is_deleted=True` (or `is_completed=True` for completed tasks).

**Why it happens:** The Sync API delta includes all state changes — additions, edits, completions, and deletions — in a single `items` array.

**How to avoid:** In Phase 3 (read-only), log and discard items where `item.get("is_deleted")` is `True`. In Phase 5+ the sync pipeline must route these to the delete/complete handler.

**Warning signs:** Attempting to fetch a task by ID that appears in the delta but returns 404.

### Pitfall 5: `sync_token` not persisted before processing items

**What goes wrong:** Service persists the new `sync_token` only after processing all items in the delta. If processing fails mid-way, the next startup re-fetches the same delta and re-processes items.

**Why it happens:** Processing is tied to persistence in the same transaction.

**How to avoid:** Persist the new `sync_token` immediately after receiving the Sync API response, before processing items. Item processing is idempotent (hash check), so double-processing is safe and preferable to losing items.

**Warning signs:** Duplicate `sync` events in `sync_events` after a crash.

### Pitfall 6: `TodoistAPIAsync` not closed at shutdown

**What goes wrong:** The `TodoistAPIAsync` instance holds an `httpx.AsyncClient` internally. If not closed at lifespan shutdown, Python emits a `ResourceWarning`.

**Why it happens:** Unlike `ZohoClient` (which creates per-call clients), `TodoistAPIAsync` holds a long-lived client.

**How to avoid:** Store the `TodoistClient` instance on `app.state`; in the lifespan `yield` cleanup section, call `await todoist_client.close()`.

**Warning signs:** `ResourceWarning: TodoistAPIAsync client was not closed` in logs.

---

## Code Examples

### Sync API call (direct httpx)

```python
# Source: [CITED: developer.todoist.com/sync/v9/]
async with httpx.AsyncClient() as client:
    resp = await client.post(
        "https://api.todoist.com/api/v1/sync",
        headers={"Authorization": f"Bearer {api_token}"},
        data={
            "sync_token": sync_token,   # "*" for full sync; token for incremental
            "resource_types": '["items"]',
        },
    )
resp.raise_for_status()
body = resp.json()
items = body.get("items", [])          # all items (may include deleted)
new_sync_token = body["sync_token"]    # persist this immediately
full_sync = body.get("full_sync", False)
```

### REST single-task fetch (SDK)

```python
# Source: [VERIFIED: todoist_api_python/api_async.py — get_task()]
from todoist_api_python.api_async import TodoistAPIAsync

api = TodoistAPIAsync(token=api_token)
task = await api.get_task("abc123")
# task.id, task.content, task.description, task.priority (1–4)
# task.due (Due|None), task.due.date (date|datetime), task.due.string
# task.labels (list[str]|None)
# task.is_completed (bool property: completed_at is not None)
# task.project_id (str)
await api.close()
```

### Startup sync_token load + full sync

```python
# Source: [ASSUMED] — pattern follows Phase 2 token load pattern
async with session_factory() as session:
    stored_token = await load_sync_token(session)   # returns "*" if missing

items, new_token = await todoist_client.fetch_sync_delta(
    sync_token=stored_token,
    project_id=settings.todoist_project_id,
)
# Persist BEFORE processing (idempotent processing; avoid losing state on crash)
async with session_factory() as session:
    await save_sync_token(session, new_token)
```

### Footer extraction

```python
# Source: [VERIFIED: app/core/normalise.py — ZOHO_ID_RE already defined]
from app.core.normalise import ZOHO_ID_RE

def extract_zoho_id(description: str | None) -> str | None:
    if not description:
        return None
    m = ZOHO_ID_RE.search(description)
    return m.group(1) if m else None

# Usage in Sync API item processing:
zoho_id = extract_zoho_id(item.get("description", ""))
if zoho_id is None:
    log.info("todoist_item_no_footer_discarded", todoist_id=item["id"])
    continue  # SYNC-8: discard items without footer
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `todoist-python` (legacy SDK, sync-only) | `todoist-api-python` 4.0.0 (ships async class) | 2024 | Async-native; no thread bridging required |
| Sync API at `api.todoist.com/sync/v9` | Sync API at `api.todoist.com/api/v1/sync` | 2024 (v1 unification) | URL changed; same semantics; `sync_token` and `resource_types` params unchanged |
| `todoist-api-python` 3.x (sync only) | `todoist-api-python` 4.0.0 (`TodoistAPIAsync`) | 2024 | v4 ships both `TodoistAPI` (sync) and `TodoistAPIAsync` (async) |

**Deprecated/outdated:**

- `todoist-python` (PyPI): Officially deprecated by Doist; last release 2019. Do not use.
- Old Sync API URL `https://api.todoist.com/sync/v9`: Now at `https://api.todoist.com/api/v1/sync`. Both may work but use the v1 URL going forward.
- `TodoistAPI` (sync class from `todoist-api-python`): Blocks the event loop; use `TodoistAPIAsync` in all async contexts.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Todoist Sync API endpoint is `https://api.todoist.com/api/v1/sync` (not the older `sync/v9` URL) | Pattern 1, Code Examples | If the old URL is still required, update URL constant; semantics are identical |
| A2 | `str(task.due.date)` produces a string accepted by `normalise_due_date()` for both `date` and `datetime` objects | Pattern 4 | If `datetime.date.__str__()` format differs from `fromisoformat()` expectations, due date normalisation silently returns `None` |
| A3 | Sync API items array uses `project_id` key (not `projectId` or another casing) for project filtering | Pattern 1 | If key name differs, client-side filter silently includes all items |
| A4 | `TodoistAPIAsync._token` is accessible (private attribute) for use in the Sync API httpx call | Pattern 1 | If attribute is name-mangled, access the token via the constructor parameter stored on `TodoistClient` instead |

---

## Open Questions (RESOLVED)

1. **Sync API project_id field name in item dict** — RESOLVED: Use `item.get("project_id")` defensively; log raw first item on startup to confirm key name. Plan 03-02 Task 2 acceptance criterion includes `test_sync_delta_filters_by_project_id`.

2. **Sync API rate limits** — RESOLVED: Handle 429 from Sync API with `TodoistRateLimitError` typed exception + test coverage. At 15-min cron intervals this should not be an issue.

3. **`TodoistAPIAsync._token` visibility** — RESOLVED: Plan 03-02 Task 1 stores `self._api_token = api_token` at init time. `fetch_sync_delta()` uses `self._api_token` directly, avoiding access to private `self._api._token`. Acceptance criterion greps for `self._api_token = api_token`.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `todoist-api-python` | REST task fetch | ✓ (installed) | 4.0.0 | — |
| `httpx` | Sync API calls | ✓ (installed) | 0.28.1 | — |
| `pytest-httpx` | Unit test mocking | ✓ (in requirements-dev.txt) | >=0.35.0 | — |
| Todoist API (live) | E2E validation | ✓ (token in env) | — | Mock httpx for unit tests |
| Postgres `kv_store` | sync_token persistence | ✓ (Phase 1 schema) | — | In-memory for unit tests |

**Missing dependencies with no fallback:** None.

**Unit test strategy:** Mock `httpx.AsyncClient` via `pytest-httpx` for both REST (`/tasks/{id}`) and Sync API (`/api/v1/sync`) calls. No live Todoist credentials needed for the test suite.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio + pytest-httpx |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/unit/test_todoist_client.py tests/unit/test_todoist_normalise.py -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SYNC-5 | `extract_zoho_id(None)` returns `None` | unit | `pytest tests/unit/test_todoist_normalise.py::test_extract_zoho_id_none -x` | ❌ Wave 0 |
| SYNC-5 | `extract_zoho_id("Title\n\n---\n[zoho:12345]")` returns `"12345"` | unit | `pytest tests/unit/test_todoist_normalise.py::test_extract_zoho_id_footer -x` | ❌ Wave 0 |
| SYNC-5 | `extract_zoho_id("[zoho:99] in middle")` returns `"99"` | unit | `pytest tests/unit/test_todoist_normalise.py::test_extract_zoho_id_mid_text -x` | ❌ Wave 0 |
| SYNC-5 | `extract_zoho_id("User edited\n\n---\n[zoho:12345]")` returns `"12345"` | unit | `pytest tests/unit/test_todoist_normalise.py::test_extract_zoho_id_after_user_edit -x` | ❌ Wave 0 |
| SYNC-5 | `extract_zoho_id("no footer")` returns `None` | unit | `pytest tests/unit/test_todoist_normalise.py::test_extract_zoho_id_missing -x` | ❌ Wave 0 |
| SYNC-8 | Sync API items without footer are discarded (not processed) | unit | `pytest tests/unit/test_todoist_client.py::test_sync_delta_filters_no_footer -x` | ❌ Wave 0 |
| SYNC-9 | `todoist_task_to_normalised()` never includes labels in `NormalisedTask` | unit | `pytest tests/unit/test_todoist_normalise.py::test_normalise_excludes_labels -x` | ❌ Wave 0 |
| SEED-7 | `load_sync_token()` returns `"*"` when kv_store has no entry | unit | `pytest tests/unit/test_todoist_client.py::test_load_sync_token_missing -x` | ❌ Wave 0 |
| SEED-7 | `load_sync_token()` returns stored token when present | unit | `pytest tests/unit/test_todoist_client.py::test_load_sync_token_present -x` | ❌ Wave 0 |
| SEED-7 | `fetch_sync_delta()` persists returned sync_token before returning | unit | `pytest tests/unit/test_todoist_client.py::test_sync_token_persisted -x` | ❌ Wave 0 |
| — | `fetch_todoist_task()` raises `TodoistAuthError` on 401 | unit | `pytest tests/unit/test_todoist_client.py::test_fetch_task_401 -x` | ❌ Wave 0 |
| — | `fetch_todoist_task()` raises `TodoistNotFoundError` on 404 | unit | `pytest tests/unit/test_todoist_client.py::test_fetch_task_404 -x` | ❌ Wave 0 |
| — | `fetch_todoist_task()` raises `TodoistRateLimitError` on 429 | unit | `pytest tests/unit/test_todoist_client.py::test_fetch_task_429 -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/unit/test_todoist_client.py tests/unit/test_todoist_normalise.py -x -q`
- **Per wave merge:** `pytest tests/unit/ -v`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `app/todoist/__init__.py` — empty package init
- [ ] `app/todoist/client.py` — `TodoistClient` + typed exceptions + `fetch_todoist_task()` + `fetch_sync_delta()`
- [ ] `app/todoist/normalise.py` — `todoist_task_to_normalised()` + `extract_zoho_id()`
- [ ] `app/todoist/sync_manager.py` — `load_sync_token()` + `save_sync_token()` + `startup_sync()`
- [ ] `tests/unit/test_todoist_client.py` — covers SYNC-8, SEED-7, error mapping
- [ ] `tests/unit/test_todoist_normalise.py` — covers SYNC-5, SYNC-9
- [ ] `app/main.py` (modify) — wire `TodoistClient` into lifespan: startup_sync + close at shutdown

*(Existing `tests/conftest.py` and `pytest-httpx` fixture available — no new dev dependencies needed.)*

---

## Security Domain

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Yes — API token management | Store `TODOIST_API_TOKEN` as env var only; never log the token value; pass to `TodoistAPIAsync` at init |
| V3 Session Management | No — stateless API token | — |
| V4 Access Control | No | — |
| V5 Input Validation | Yes — Sync API item dicts | Use `.get()` with defaults on all dict field accesses; do not assume key presence |
| V6 Cryptography | No | — |

### Known Threat Patterns for Todoist API

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| API token logged in plaintext | Information Disclosure | Never pass `TODOIST_API_TOKEN` to structlog; log only first 8 chars + `...` for debugging if needed |
| 401 silently retried | Denial of Service | `TodoistAuthError` must stop sync and alert, not retry; mirrors `ZohoAuthError` pattern |
| Sync API items from other projects processed | Tampering | Always filter `item["project_id"] == settings.todoist_project_id` client-side |

---

## Sources

### Primary (HIGH confidence)

- [VERIFIED: /data/home/.local/lib/python3.11/site-packages/todoist_api_python/api_async.py] — `TodoistAPIAsync` class, `get_task()` method signature, async context manager, `close()` method
- [VERIFIED: /data/home/.local/lib/python3.11/site-packages/todoist_api_python/models.py] — `Task` dataclass fields: `id`, `content`, `description`, `priority`, `due` (`Due|None`), `labels`, `is_completed` property, `project_id`
- [VERIFIED: /data/home/.local/lib/python3.11/site-packages/todoist_api_python/models.py] — `Due` dataclass: `.date` typed as `ApiDue` (union `date | datetime`)
- [VERIFIED: app/core/normalise.py] — `ZOHO_ID_RE` regex already defined and exported: `re.compile(r"\[zoho:(\d+)\]")`
- [VERIFIED: app/zoho/token_manager.py] — `upsert_kv()` available and importable for `sync_token` persistence
- [VERIFIED: app/db/models.py] — `KVStore` table exists with `key`, `value` columns
- [CITED: developer.todoist.com/sync/v9/] — Sync API endpoint `POST https://api.todoist.com/api/v1/sync`; `sync_token` param; `resource_types` param; response format (`items`, `sync_token`, `full_sync`)

### Secondary (MEDIUM confidence)

- [WebSearch + CITED: developer.todoist.com/sync/v9/] — Sync API URL is `api/v1/sync` (not `sync/v9`); confirmed current endpoint

### Tertiary (LOW confidence)

- [ASSUMED] — `str(task.due.date)` produces a string compatible with `normalise_due_date()` for both `date` and `datetime` objects (A2 above)
- [ASSUMED] — Sync API item dict uses `project_id` key for client-side filtering (A3 above)

---

## Project Constraints (from CLAUDE.md)

| Directive | How It Applies to Phase 3 |
|-----------|--------------------------|
| Python 3.12 (Railway) / 3.11+ local | `todoist-api-python` 4.0.0 supports 3.11+ ✓ |
| FastAPI + arq stack | `TodoistClient` wired into FastAPI lifespan; no blocking calls |
| Postgres on Railway | `kv_store` used for `sync_token` persistence; same pattern as Phase 2 |
| All secrets as env vars | `TODOIST_API_TOKEN` from `get_settings()` |
| No UI | Phase 3 has no UI concerns |
| GSD workflow for non-trivial work | Applies to this phase execution |

---

## Metadata

**Confidence breakdown:**
- `TodoistAPIAsync` API surface: HIGH — verified from installed source code
- `Task` dataclass fields: HIGH — verified from installed models.py
- Sync API endpoint and format: HIGH — verified from official documentation
- Sync API project_id filtering (client-side): MEDIUM — documented behavior, key name assumed
- `str(task.due.date)` normalisation compatibility: LOW — assumed; first test run will confirm

**Research date:** 2026-04-24
**Valid until:** 2026-05-24 (Todoist API is stable; `todoist-api-python` version pinned in requirements.txt)
