# Phase 2: Zoho Read - Pattern Map

**Mapped:** 2026-04-23
**Files analyzed:** 5 new files + 1 modified file
**Analogs found:** 5 / 6

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `app/zoho/__init__.py` | package-init | — | `app/core/__init__.py` | exact |
| `app/zoho/client.py` | service | request-response | `app/core/hash.py` + research patterns | role-partial (no HTTP service analog yet) |
| `app/zoho/token_manager.py` | service | event-driven (asyncio loop) | `app/main.py` (lifespan pattern) | partial (same async context, different operation) |
| `app/zoho/normalise.py` | utility | transform | `app/core/normalise.py` | exact |
| `tests/unit/test_zoho_client.py` | test | request-response | `tests/unit/test_normalise.py` + `tests/unit/test_hash.py` | role-match |
| `requirements-dev.txt` (modify) | config | — | `requirements-dev.txt` (existing) | exact |

---

## Pattern Assignments

### `app/zoho/__init__.py` (package-init)

**Analog:** `app/core/__init__.py` (line 1 — empty file, one line)

**Pattern:** Empty `__init__.py` — no imports, no re-exports. Phase 1 established that sub-packages use bare `__init__.py` files.

```python
# app/zoho/__init__.py
# (empty)
```

---

### `app/zoho/client.py` (service, request-response)

**Analog:** No existing HTTP client in the codebase. Use research patterns directly (all from RESEARCH.md — see Pattern 1 and Pattern 3 and Pattern 5).

**Imports pattern** — follow `app/core/hash.py` lines 1-4 and `app/core/normalise.py` lines 1-6 for import style:

```python
# app/core/hash.py lines 1-4 — import style: stdlib first, then internal
import hashlib
import json
from .normalise import NormalisedTask
```

```python
# app/core/normalise.py lines 1-6 — dataclass + typing pattern
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
```

**Applied to client.py imports:**
```python
# app/zoho/client.py
import httpx
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
```

**Typed exception pattern** — project has no existing exception hierarchy; establish from scratch per research Pattern 1:

```python
class ZohoAuthError(Exception):
    """Raised on 401 — refresh token invalid or scope mismatch."""

class ZohoNotFoundError(Exception):
    """Raised on 404 — task does not exist."""

class ZohoRateLimitError(Exception):
    """Raised on 429 — concurrency limit exceeded."""

class ZohoAPIError(Exception):
    """Raised on other non-2xx responses."""
```

**Core dataclass pattern** — follow `app/core/normalise.py` line 10 for frozen dataclass:

```python
# app/core/normalise.py line 10-15 — frozen dataclass as value object
@dataclass(frozen=True)
class NormalisedTask:
    title: str
    due_date: str | None
    priority: int
    is_completed: bool
```

`ZohoClient` uses a plain (mutable) `@dataclass` because `access_token` is updated in-place:

```python
@dataclass
class ZohoClient:
    access_token: str
    base_url: str = "https://www.zohoapis.eu/crm/v8"
```

**Error-handling / `_handle` pattern** (research Pattern 1, lines 172-180):

```python
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

**204-as-empty-result pattern** (research Pitfall 2 + Pattern 5):

```python
if resp.status_code == 204:
    break   # valid empty result — do NOT call _handle
```

**Pagination termination pattern** (research "Don't Hand-Roll" section):

```python
if not body.get("info", {}).get("more_records"):
    break
```

**Field metadata method pattern** (research Pattern 3, lines 263-297):

```python
async def get_fields_metadata(self, module: str = "Tasks") -> dict:
    # Use field.get("field_label") NOT field.get("display_value") — see Pitfall 6
    # Returns {"todoist_task_id_api_name": str | None, "status_picklist_values": list[str]}
```

---

### `app/zoho/token_manager.py` (service, event-driven)

**Analog:** `app/main.py` — the only existing async lifecycle code.

**lifespan / asyncio context pattern** (`app/main.py` lines 1-22):

```python
# app/main.py lines 1-22 — FastAPI lifespan context manager
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    log.info("startup", zoho_region=settings.zoho_region, ...)
    yield
    log.info("shutdown")
```

**Logging pattern** — all Phase 1 modules use `get_logger(__name__)` and structlog keyword-arg style:

```python
# app/core/logging.py lines 25-26 — logger factory
def get_logger(name: str):
    return structlog.get_logger(name)

# Usage pattern across Phase 1 files:
log = get_logger(__name__)
log.info("zoho_token_refreshed", expires_at=new_expires_at.isoformat())
log.error("zoho_token_refresh_failed", error=str(exc))
```

**Settings access pattern** (`app/core/config.py` lines 30-38):

```python
# app/core/config.py lines 30-38 — always call get_settings() not module-level settings
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
```

Token manager must call `get_settings()` (not import `settings` directly) so tests can patch it:

```python
from app.core.config import get_settings
settings = get_settings()
```

**Core refresh loop pattern** (research Pattern 2, lines 229-251):

```python
async def proactive_refresh_loop(token_state: dict, db_session_factory):
    settings = get_settings()
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECS)
        try:
            new_token, new_expires_at = await refresh_access_token(settings)
            token_state["access_token"] = new_token
            token_state["expires_at"] = new_expires_at
            async with db_session_factory() as session:
                await upsert_kv(session, "zoho_access_token", new_token)
                await upsert_kv(session, "zoho_token_expires_at", new_expires_at.isoformat())
            log.info("zoho_token_refreshed", expires_at=new_expires_at.isoformat())
        except Exception as exc:
            log.error("zoho_token_refresh_failed", error=str(exc))
            raise  # kills the task; process health check will detect
```

**Security constraint** (research Security Domain section): Never log the access token value. Log only `expires_at` and first 8 chars at most.

---

### `app/zoho/normalise.py` (utility, transform)

**Analog:** `app/core/normalise.py` — exact structural match (pure functions + dataclass adapter).

**Import pattern** (`app/core/normalise.py` lines 1-7 + `app/core/priority.py` lines 1-2):

```python
# app/core/normalise.py lines 1-7 — import style for transform utility
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
```

```python
# app/core/priority.py lines 1-3 — comment header + module-level constants
# app/core/priority.py
# Zoho priority string → Todoist priority integer
ZOHO_TO_TODOIST: dict[str | None, int] = { ... }
```

**Core transform function pattern** (research Pattern 4):

```python
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

Note: This module re-uses `normalise_due_date`, `normalise_title`, and `zoho_to_todoist_priority` from Phase 1 — no re-implementation.

---

### `tests/unit/test_zoho_client.py` (test, request-response)

**Analog:** `tests/unit/test_normalise.py` and `tests/unit/test_hash.py` — exact structural match for unit test style.

**Import pattern** (`tests/unit/test_normalise.py` line 2 and `tests/unit/test_hash.py` lines 1-5):

```python
# tests/unit/test_normalise.py line 2 — flat direct imports, no fixtures needed
from app.core.normalise import normalise_due_date, normalise_title, strip_footer, ZOHO_ID_RE
```

```python
# tests/unit/test_hash.py lines 1-5 — multi-module imports at top
from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title
from app.core.hash import canonical_hash
from app.core.priority import zoho_to_todoist_priority
```

**Helper factory pattern** (`tests/unit/test_hash.py` lines 7-13 — `make_task()` helper):

```python
def make_task(title="Buy milk", due="2026-05-01", priority=2, completed=False):
    return NormalisedTask(
        title=normalise_title(title),
        due_date=normalise_due_date(due),
        priority=priority,
        is_completed=completed,
    )
```

Apply the same pattern: `make_mock_response(status_code, json_body)` helper to avoid repeating httpx mock setup.

**Test naming pattern** — descriptive snake_case names that state the behavior:

```python
# tests/unit/test_normalise.py — naming examples
def test_date_only_passthrough():   ...
def test_none_due_date():           ...
def test_strip_footer_none():       ...
```

**conftest.py fixture pattern** (`tests/conftest.py` lines 6-23):

```python
# tests/conftest.py lines 6-23 — env fixture pattern (apply for tests needing settings)
REQUIRED_ENV = { "ZOHO_CLIENT_ID": "test-client-id", ... }

@pytest.fixture
def complete_env(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    return REQUIRED_ENV
```

**httpx mocking** — `pytest-httpx` (to be added to `requirements-dev.txt`). Use `httpx_mock` fixture to intercept requests without live credentials. This is the only new test infrastructure needed.

```python
# Pattern for test_zoho_client.py using pytest-httpx:
def test_get_task_404(httpx_mock):
    httpx_mock.add_response(status_code=404)
    client = ZohoClient(access_token="test-token")
    import pytest
    with pytest.raises(ZohoNotFoundError):
        import asyncio
        asyncio.run(client.get_task("999"))
```

**pytest-asyncio pattern** (`pyproject.toml` line 9: `asyncio_mode = "auto"`):

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

`asyncio_mode = "auto"` is already configured — async test functions work without `@pytest.mark.asyncio` decorator.

---

### `requirements-dev.txt` (modify — add pytest-httpx)

**Analog:** `requirements-dev.txt` (existing, lines 1-2):

```
pytest==9.0.2
pytest-asyncio
```

**Modification:** Add `pytest-httpx` for mocking `httpx.AsyncClient` in unit tests. Pin to a version compatible with `httpx==0.28.1`.

```
pytest==9.0.2
pytest-asyncio
pytest-httpx>=0.35.0
```

---

## Shared Patterns

### Settings / Config Access
**Source:** `app/core/config.py` lines 30-38
**Apply to:** `app/zoho/client.py`, `app/zoho/token_manager.py`

```python
from app.core.config import get_settings
# Always call get_settings() — not the module-level `settings` alias.
# This allows tests to patch via get_settings.cache_clear() + monkeypatch.setenv().
settings = get_settings()
```

### Structured Logging
**Source:** `app/core/logging.py` lines 25-26, usage across `app/main.py`
**Apply to:** `app/zoho/client.py`, `app/zoho/token_manager.py`

```python
from app.core.logging import get_logger
log = get_logger(__name__)

# Keyword-arg style (structlog convention used throughout Phase 1):
log.info("event_name", key=value, key2=value2)
log.error("event_name", error=str(exc))
```

### KVStore Model (token persistence)
**Source:** `app/db/models.py` lines 43-49
**Apply to:** `app/zoho/token_manager.py` (upsert_kv helper)

```python
# app/db/models.py lines 43-49 — KVStore table used for token persistence
class KVStore(Base):
    __tablename__ = "kv_store"
    key        = Column(String, primary_key=True)
    value      = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                        onupdate=func.now())
```

The token manager stores `zoho_access_token` and `zoho_token_expires_at` as string values in this table.

### FastAPI Lifespan Integration
**Source:** `app/main.py` lines 9-22
**Apply to:** `app/main.py` (modify to start token refresh background task + resolve field metadata)

```python
# app/main.py lines 9-22 — lifespan context manager to extend with Phase 2 startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    log.info("startup", ...)
    # Phase 2 additions go here (before yield):
    #   1. Load token from kv_store
    #   2. Refresh immediately if expired
    #   3. Resolve todoist_task_id_api_name
    #   4. Start proactive_refresh_loop as asyncio.create_task()
    yield
    log.info("shutdown")
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `app/zoho/client.py` (HTTP methods) | service | request-response | No existing HTTP client service in the codebase; Phase 1 is pure data transformation + DB schema with no outbound HTTP |

For `client.py` HTTP methods, the planner must use research Pattern 1, Pattern 3, and Pattern 5 from `02-RESEARCH.md` directly. These are high-confidence patterns cited from official Zoho API docs.

---

## Metadata

**Analog search scope:** `/data/home/zoho-todoist-sync/app/`, `/data/home/zoho-todoist-sync/tests/`
**Files scanned:** 12 Python source files, 7 test files, `pyproject.toml`, `requirements*.txt`
**Pattern extraction date:** 2026-04-23
