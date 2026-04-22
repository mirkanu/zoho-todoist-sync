# Stack Research

**Project:** zoho-todoist-sync
**Researched:** 2026-04-22
**Method:** Training knowledge (cutoff August 2025) + project constraints from PROJECT.md
**Note:** WebSearch, WebFetch, and Bash tools were unavailable in this environment. All version
claims are from training data and MUST be verified against PyPI before pinning in
requirements.txt. Confidence levels are assigned honestly throughout.

---

## Core Stack Validation

### 1. Zoho CRM Python SDK (`zohocrmsdk`)

**Verdict:** Use it, but wrap it defensively. It works, with caveats.

**Package name:** `zohocrmsdk` on PyPI (previously `zcrmsdk` — the old name is abandoned; do
not use it). The current package is published by Zoho Corporation directly.

**Maintenance status (MEDIUM confidence):** Zoho publishes updates reactively rather than on
a regular cadence. The SDK is not community-maintained — it's Zoho's own tooling. Commits
happen when Zoho changes their API. For stable modules like Tasks this is fine; for cutting-edge
V8 features you may lag. The Tasks module (`zohocrmsdk.src.com.zoho.crm.api.tasks`) is a
first-class module and is not at risk of being dropped.

**OAuth token refresh:** The SDK's built-in token management (`OAuthToken` + a persistence
store) handles refresh automatically if you provide a `TokenStore` implementation. The SDK
ships a `DBStore` (MySQL) and a `FileStore`. For this project, implement a custom
`TokenStore` that persists tokens to Postgres using the same SQLAlchemy connection — do not
use `FileStore` in a Railway environment where the filesystem is ephemeral. The token refresh
flow:

```python
from zohocrmsdk.src.com.zoho.crm.api.util.api_response import APIResponse
from zohocrmsdk.src.com.zoho.crm.api.initializer import Initializer
from zohocrmsdk.src.com.zoho.crm.api.dc import USDataCenter
from zohocrmsdk.src.com.zoho.crm.api.oauth_token import OAuthToken

# SDK auto-refreshes via your TokenStore.get_token() / save_token() pair.
# Call Initializer.initialize() once at app startup, not per request.
```

The SDK uses a thread-local `Initializer` — this is fine for FastAPI if you call
`Initializer.initialize()` once at startup. Do not call it inside async request handlers
without understanding the thread-safety model. The SDK is synchronous; wrap calls in
`asyncio.get_event_loop().run_in_executor(None, sdk_call)` or run them inside the arq
worker (which does not require async-all-the-way-down).

**Webhook verification (LOW confidence):** Zoho CRM workflow webhooks do not ship a
cryptographic signature header comparable to Todoist's `X-Todoist-Hmac-SHA256`. Zoho's
webhook calls are IP-restricted optionally, but the primary security model is a shared secret
in the URL path or a custom header you configure in the Workflow rule. Verify your Zoho
webhook URL includes a non-guessable path token (`/webhook/zoho/{secret_token}`). Do not
rely on IP allowlisting alone on Railway, as Railway's egress IPs change.

**Tasks module quirks (MEDIUM confidence):**
- Zoho returns due dates as `YYYY-MM-DDTHH:MM:SS+05:30` (or similar TZ offset) even for
  date-only fields. The PROJECT.md already captures this. Always normalise: split on `T`,
  take the date portion only.
- The Tasks module search uses `SearchRecords` with criteria like
  `(Owner:equals:{user_id})AND(Status:not_equal_to:Completed)` — not a dedicated endpoint.
- Pagination: `SearchRecords` returns up to 200 records per page. Use `page` + `per_page`
  parameters for the seed reconciliation sweep.

**Recommendation:** Pin to the latest stable version. Wrap all SDK calls in a thin
`ZohoClient` service class that handles executor offload, error logging, and retry on 429.

---

### 2. Todoist Python SDK (`todoist-api-python`)

**Verdict:** Use it for REST operations. Drop to raw `httpx` for Sync API with `sync_token`.

**Package name:** `todoist-api-python` on PyPI. Maintained by Doist (the Todoist team).
Actively maintained as of mid-2025 (MEDIUM confidence).

**REST vs Sync API coverage:**
- `todoist-api-python` wraps the **REST API v2** (`https://api.todoist.com/rest/v2/`). This
  covers: create task, get task, update task, close task, delete task, get project tasks.
- The **Sync API** (`https://api.todoist.com/sync/v9/`) — which supports incremental
  `sync_token` polling — is NOT wrapped by `todoist-api-python`. The SDK has no
  `sync_token`-aware client.

**Right client for each use case:**

| Use case | Client | Why |
|---|---|---|
| Webhook receipt (Todoist → our endpoint) | No SDK needed; parse JSON body directly | Todoist sends full item payload in webhook |
| Create/update/close/delete a task | `todoist-api-python` `TodoistAPI` | Clean wrapper, handles auth |
| Reconciliation sweep (incremental) | Raw `httpx` POST to Sync API | `sync_token` not in SDK |
| Get single task by ID | `todoist-api-python` `api.get_task(task_id)` | Simple REST GET |

**Sync API reconciliation pattern:**
```python
import httpx

async def sync_todoist(sync_token: str, token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.todoist.com/sync/v9/sync",
            headers={"Authorization": f"Bearer {token}"},
            json={"sync_token": sync_token, "resource_types": ["items"]},
        )
        r.raise_for_status()
        return r.json()  # .sync_token, .items (full_sync or incremental)
```

Store the returned `sync_token` in Postgres after each sweep. On startup, use `"*"` to force
a full sync and seed the `sync_state` table.

**Webhook verification:** Todoist signs webhook payloads with HMAC-SHA256. The header is
`X-Todoist-Hmac-SHA256`. Verification:
```python
import hmac, hashlib, base64

def verify_todoist_webhook(body: bytes, signature: str, client_secret: str) -> bool:
    expected = base64.b64encode(
        hmac.new(client_secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)
```
FastAPI: read `Request.body()` before any JSON parsing — the raw bytes are what's signed.

**Rate limits:** REST API has no published hard limit but enforces per-user throttling.
Sync API: 450 requests per 15 minutes per user token. Batch REST writes where possible.

---

### 3. arq (Async Redis Queue)

**Verdict:** Correct choice. Lighter than Celery for this workload. Use with Redis 7+.

**Job deduplication by key:** arq supports `job_id` parameter on `redis.enqueue_job()`. If
you enqueue a job with the same `job_id` while one is already queued/in-progress, arq
returns the existing job without creating a duplicate. This is the core mechanism for
preventing concurrent webhook races on the same Zoho task.

```python
# In the FastAPI webhook handler:
await redis.enqueue_job(
    "process_zoho_task",
    zoho_task_id,
    _job_id=f"zoho-task-{zoho_task_id}",  # dedup key
    _queue_name="default",
)
```

If the same task fires two webhooks within milliseconds, the second `enqueue_job` call
returns the already-queued job rather than creating a second. This prevents double-processing
without any additional locking.

**Retry configuration:** arq retries failed jobs based on `max_tries` in the function
definition. Configure per-function:

```python
async def process_zoho_task(ctx, zoho_task_id: str):
    ...

process_zoho_task.max_tries = 5
process_zoho_task.retry_delays = [5, 30, 120, 300]  # seconds between retries
```

For this project, retry on transient API errors (429, 5xx) but do NOT retry on:
- 404 (task deleted between webhook and dequeue — expected, log and skip)
- 401 (token expired and refresh failed — alert, don't retry blindly)

**Worker class pattern:**

```python
from arq import Worker
from arq.connections import RedisSettings

class WorkerSettings:
    functions = [process_zoho_task, process_todoist_webhook, run_reconciliation]
    redis_settings = RedisSettings.from_dsn(os.environ["REDIS_URL"])
    max_jobs = 10
    job_timeout = 300  # seconds
    health_check_interval = 60
```

**Known issues in 2024-2025 (MEDIUM confidence):**
- arq's `job_id`-based deduplication has a race: if job A finishes and job B is enqueued
  with the same `job_id` before arq cleans up A's Redis key, B might also get deduplicated
  against A (i.e., dropped). The window is very short (< 1s). For this sync use case, this
  is acceptable — the next reconciliation sweep will catch any missed update.
- arq does not support priority queues natively. For this project, a single queue is
  sufficient.
- The `health_check_key` in arq stores a timestamp in Redis; expose this in the `/health`
  endpoint by reading the arq health key directly.

**Railway worker deployment:** arq workers run as a separate process (`arq
worker.WorkerSettings`). Do not run the worker inside the FastAPI process. See Deployment
Architecture section.

---

### 4. FastAPI

**Verdict:** Correct choice. Standard async Python web framework.

**Key patterns for this project:**

- Use `lifespan` (not deprecated `on_event`) for startup/shutdown:
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: init DB pool, init arq redis pool, init Zoho SDK
    app.state.arq_redis = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
    app.state.db = await create_async_engine(os.environ["DATABASE_URL"])
    yield
    # shutdown: close pools
    await app.state.arq_redis.close()

app = FastAPI(lifespan=lifespan)
```

- Webhook endpoints must read raw request body before parsing to enable HMAC verification.
  Use `Request` + `await request.body()`:
```python
@app.post("/webhook/todoist/{secret}")
async def todoist_webhook(secret: str, request: Request):
    body = await request.body()
    sig = request.headers.get("X-Todoist-Hmac-SHA256", "")
    if not verify_todoist_webhook(body, sig, settings.TODOIST_CLIENT_SECRET):
        raise HTTPException(status_code=401)
    payload = json.loads(body)
    ...
```

- Return `200 OK` from webhook handlers immediately after enqueuing — do not await the job.
  Zoho and Todoist both retry on non-2xx responses. A slow handler that awaits the sync
  worker will cause retries under load.

---

### 5. Resend Python SDK

**Verdict:** Use it. Correct package name is `resend`.

**Package name:** `resend` on PyPI (not `resend-python`, not `resend-sdk`).

**Version context:** The YNAB project on the same Railway account uses `resend` v6.9.4 per
PROJECT.md. Use the same version for consistency unless there is a specific reason to
upgrade. The `resend` package's API has been stable across v6.x (MEDIUM confidence).

**Usage pattern:**
```python
import resend

resend.api_key = os.environ["RESEND_API_KEY"]

def send_notification(subject: str, html: str) -> None:
    resend.Emails.send({
        "from": "sync@yourdomain.com",
        "to": ["manuelkuhs@gmail.com"],
        "subject": subject,
        "html": html,
    })
```

For this project, notifications are fire-and-forget (task reassigned away, Todoist task
deleted). Call from inside the arq worker after the primary sync operation succeeds. Do not
make email a blocker for the sync operation itself — log failures but do not retry-loop on
email errors.

**Version to pin:** `resend==6.9.4` (match YNAB project for consistency; verify this is the
latest stable before pinning).

---

### 6. Postgres on Railway + SQLAlchemy

**Verdict:** Use `asyncpg` driver with SQLAlchemy async engine. Do not use connection
pooling middleware (PgBouncer) for this workload.

**Connection setup:**
```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

engine = create_async_engine(
    os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://"),
    pool_size=5,         # FastAPI web process
    max_overflow=5,
    pool_pre_ping=True,  # handles Railway's connection resets
    pool_recycle=300,    # recycle connections every 5 min (Railway closes idle after ~10 min)
)
AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
```

**FastAPI vs arq connection pooling:**
- FastAPI web process: use the async engine above with `pool_size=5`. Webhook handlers
  do minimal DB work (read sync_state, enqueue job) so 5 connections is enough.
- arq worker process: create a **separate** engine instance in the worker startup context
  (`ctx["db"]` pattern). The worker is a separate OS process so it has its own connection
  pool. Configure `pool_size=3, max_overflow=2` for the worker — it processes one job at a
  time by default (`max_jobs=10` but jobs share the pool).

**Railway-specific gotcha:** Railway Postgres plans have a hard connection limit (25 on
Hobby). With two processes (web + worker), each with a pool of 5-10, you are well within
limits. If you add a third process (scheduler), check the limit.

**Do not use synchronous SQLAlchemy** in the FastAPI process — mixing sync blocking I/O
with asyncio's event loop causes request stalls. The arq worker is an asyncio event loop
itself; use the async engine there too.

**Migrations:** Use Alembic. Run migrations as a Railway deploy step (before the web/worker
processes start). Add to `railway.json` or the service's "Deploy Command":
```
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

---

## Deployment Architecture

### Recommendation: Two Separate Railway Services

Run the FastAPI web process and the arq worker as **two separate Railway services** within
the same project, sharing the same Postgres and Redis.

**Why separate, not a single service:**
- Railway services run a single command. A `Procfile` with multiple entries (web + worker)
  is not natively supported — Railway picks the `web` entry and ignores others unless you
  use a custom startup script that forks processes.
- Keeping them separate gives independent scaling, independent restart on failure, and
  independent deploy logs.
- A crashed arq worker does not take down the web process (webhook receiver). This is
  critical for reliability — if the worker is OOM-killed, webhooks still land in the queue.

**Service layout:**

| Service | Start command | What it does |
|---|---|---|
| `zoho-todoist-web` | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | Receives webhooks, serves /health |
| `zoho-todoist-worker` | `python -m arq app.worker.WorkerSettings` | Processes sync jobs |

Both services share the same Railway environment variables (Postgres URL, Redis URL, API keys).
Both are deployed from the same GitHub repo. Use Railway's "Shared Variables" or duplicate
env vars across both services.

**Scheduler (reconciliation):** The reconciliation sweep (incremental Zoho + Todoist sync
every N minutes) should be an arq cron job, not a separate service. arq supports cron:

```python
from arq.cron import cron

class WorkerSettings:
    cron_jobs = [
        cron(run_reconciliation, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]
```

This runs `run_reconciliation` every 5 minutes inside the worker process. No Railway
scheduled service needed.

**Alternative (if you want one service):** Use a shell entrypoint that starts both:
```bash
#!/bin/bash
# start.sh
uvicorn app.main:app --host 0.0.0.0 --port $PORT &
python -m arq app.worker.WorkerSettings
```
Railway's healthcheck will hit `$PORT`, so the web process must start first. This works but
loses the crash isolation benefit. Not recommended.

**Dockerfile vs Nixpacks:** Railway auto-detects Python projects via Nixpacks (detects
`requirements.txt` or `pyproject.toml`). For this project, Nixpacks is sufficient — no
Dockerfile needed. Ensure `python-version` is pinned via a `.python-version` file
containing `3.12`.

**Railway gotchas for Python:**
- Railway injects `PORT` as an environment variable. Always bind to `$PORT`, not a hardcoded
  port. FastAPI/uvicorn: `--port $PORT`.
- Railway restarts services on crash with exponential backoff. The arq worker will auto-
  reconnect to Redis on restart because arq recreates the connection pool at startup.
- Do not use `--reload` in production uvicorn. Use `--workers 1` (single worker is fine;
  async handles concurrency).
- Railway's filesystem is ephemeral. Do not write anything to disk that needs to survive
  restarts (tokens, sync state, etc.) — all state goes to Postgres or Redis.

---

## Library Versions (as of research date)

**IMPORTANT:** These versions are from training data (cutoff August 2025). Verify each
against PyPI before pinning. Run `pip index versions <package>` or check pypi.org.

| Package | Recommended pin | Confidence | Verify at |
|---|---|---|---|
| `zohocrmsdk` | `>=3.0.0,<4` (check latest 3.x) | LOW — verify PyPI | pypi.org/project/zohocrmsdk |
| `todoist-api-python` | `>=2.1.3,<3` | MEDIUM | pypi.org/project/todoist-api-python |
| `arq` | `>=0.26.0,<1` | MEDIUM | pypi.org/project/arq |
| `fastapi` | `>=0.111.0,<1` | MEDIUM | pypi.org/project/fastapi |
| `uvicorn[standard]` | `>=0.30.0,<1` | MEDIUM | pypi.org/project/uvicorn |
| `httpx` | `>=0.27.0,<1` | MEDIUM | for Sync API calls |
| `sqlalchemy[asyncio]` | `>=2.0.30,<3` | HIGH | SQLAlchemy 2.x async is stable |
| `asyncpg` | `>=0.29.0,<1` | MEDIUM | asyncpg driver for SQLAlchemy |
| `alembic` | `>=1.13.0,<2` | MEDIUM | migrations |
| `resend` | `==6.9.4` | MEDIUM | match YNAB project; verify PyPI |
| `pydantic-settings` | `>=2.3.0,<3` | HIGH | config from env vars |
| `python-dotenv` | `>=1.0.0,<2` | HIGH | local dev only |

**Python version:** 3.12 (per PROJECT.md). Pin via `.python-version` file.

**Do not use:**
- `redis` (synchronous) — use `redis.asyncio` or arq's built-in async client
- `psycopg2` — use `asyncpg` for async, or `psycopg` v3 if you need synchronous
- `zcrmsdk` — the old package name, unmaintained

---

## Configuration Patterns

### Zoho SDK Initialization

The SDK must be initialized once at process startup. It is NOT thread-safe to call
`Initializer.initialize()` multiple times concurrently. Use a module-level guard:

```python
# app/integrations/zoho.py
import threading
from zohocrmsdk.src.com.zoho.crm.api.initializer import Initializer
from zohocrmsdk.src.com.zoho.crm.api.oauth_token import OAuthToken
from zohocrmsdk.src.com.zoho.crm.api.dc import USDataCenter  # or EUDataCenter

_init_lock = threading.Lock()
_initialized = False

def init_zoho_sdk(token_store: "PostgresTokenStore") -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        token = OAuthToken(
            client_id=settings.ZOHO_CLIENT_ID,
            client_secret=settings.ZOHO_CLIENT_SECRET,
            refresh_token=settings.ZOHO_REFRESH_TOKEN,
            token_store=token_store,
        )
        Initializer.initialize(
            environment=USDataCenter.PRODUCTION(),
            token=token,
        )
        _initialized = True
```

The `PostgresTokenStore` must implement `get_token(token)` and `save_token(token)` using
synchronous DB calls (the SDK is synchronous). Use a sync SQLAlchemy engine or a dedicated
sync session for the token store only.

### arq Redis Pool (shared with FastAPI)

arq uses the `redis.asyncio` client internally. Create the pool once and share it:

```python
# In FastAPI lifespan startup:
from arq.connections import create_pool, RedisSettings

app.state.arq = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))

# In webhook handler:
await request.app.state.arq.enqueue_job(
    "process_zoho_task",
    task_id,
    _job_id=f"zoho:{task_id}",
)
```

### Environment Variables

```bash
# Zoho OAuth
ZOHO_CLIENT_ID=
ZOHO_CLIENT_SECRET=
ZOHO_REFRESH_TOKEN=       # Long-lived refresh token from Zoho Developer Console
ZOHO_USER_ID=             # Zoho CRM user ID to filter tasks by assignee
ZOHO_WEBHOOK_SECRET=      # Random token embedded in webhook URL path

# Todoist
TODOIST_API_TOKEN=        # Personal API token (not OAuth for single-user)
TODOIST_CLIENT_SECRET=    # From Todoist app config, for webhook HMAC verification
TODOIST_PROJECT_ID=       # Target project ID

# Infrastructure
DATABASE_URL=             # postgresql://user:pass@host:5432/db (Railway injects this)
REDIS_URL=                # redis://default:pass@host:6379 (Railway injects this)

# Resend
RESEND_API_KEY=           # From Railway YNAB service shared variables
RESEND_FROM_EMAIL=        # Verified sender domain

# App
NOTIFICATION_EMAIL=manuelkuhs@gmail.com
```

### pydantic-settings Config Class

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    zoho_client_id: str
    zoho_client_secret: str
    zoho_refresh_token: str
    zoho_user_id: str
    zoho_webhook_secret: str
    todoist_api_token: str
    todoist_client_secret: str
    todoist_project_id: str
    database_url: str
    redis_url: str
    resend_api_key: str
    resend_from_email: str
    notification_email: str = "manuelkuhs@gmail.com"

    class Config:
        env_file = ".env"

settings = Settings()
```

---

## What to Avoid

### Do not use `zcrmsdk`
The old PyPI package name. Abandoned. The current package is `zohocrmsdk`.

### Do not use the Zoho SDK's built-in `FileStore` for token persistence
Railway's filesystem is ephemeral. Tokens written to disk are lost on restart, causing every
cold start to fail with invalid token errors. Use a custom `TokenStore` backed by Postgres.

### Do not run arq worker inside the FastAPI process
Calling `arq.Worker(...).run()` inside an async FastAPI lifespan creates a nested event loop
conflict on some Python versions, and more importantly collapses crash isolation. A crashed
worker kills the webhook receiver. Run them as separate processes/services.

### Do not use Celery
Celery adds broker complexity (separate broker format on top of Redis), requires separate
result backend configuration, and has a heavier footprint than arq. arq's job deduplication
via `_job_id` is the key feature needed here; Celery's equivalent (`task.apply_async(task_id=...)`)
is clunkier. The PROJECT.md decision is sound.

### Do not use `todoist-api-python` for Sync API calls
The SDK only wraps REST API v2. Using it for Sync API calls would require monkey-patching or
subclassing. Use raw `httpx` for Sync API; use the SDK for REST. Mixing both in a thin
`TodoistClient` wrapper class keeps the boundary clean.

### Do not use synchronous SQLAlchemy in the FastAPI process
`create_engine()` with psycopg2 in an async FastAPI handler blocks the event loop during
I/O. Always use `create_async_engine()` + `asyncpg`. The one exception is the Zoho SDK
token store, which is synchronous by nature — isolate it to a single sync session factory
used only for token operations.

### Do not store `sync_token` in Redis
`sync_token` is durable state. Redis on Railway does not guarantee persistence across
restarts (depends on plan/config). Store `sync_token` in Postgres. Redis is for job queue
only.

### Do not use `--reload` in production uvicorn
The `--reload` flag runs a file-watching subprocess that does not work correctly in
containerised environments and doubles memory usage. Railway restarts services on crash
anyway.

### Do not call `Initializer.initialize()` per-request
The Zoho SDK `Initializer` is a global singleton. Re-initializing it per-request causes
thread contention and token store churn. Initialize once at startup.

### Do not use Todoist OAuth for a single-user deployment
Todoist supports both OAuth (for multi-user apps) and personal API tokens (for single-user).
This sync runs under one Todoist account. Use the personal API token — no OAuth flow needed,
no token refresh complexity on the Todoist side.

---

## Sources

All findings are from training knowledge (model cutoff August 2025). Confidence levels are
assigned based on: HIGH = well-established, stable, widely documented; MEDIUM = known but
should be verified against current docs; LOW = uncertain, must verify before implementation.

**Must verify before implementation:**
- `zohocrmsdk` latest version and Tasks module API surface (pypi.org/project/zohocrmsdk)
- `todoist-api-python` latest version (pypi.org/project/todoist-api-python)
- arq latest version and `_job_id` dedup behaviour in current release (pypi.org/project/arq,
  github.com/samuelcolvin/arq)
- Resend SDK current version and `Emails.send()` interface (pypi.org/project/resend,
  resend.com/docs)
- Railway Postgres connection limits for the relevant plan tier (docs.railway.app)
