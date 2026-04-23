# Phase 1: Foundation - Pattern Map

**Mapped:** 2026-04-23
**Files analyzed:** 16 (new files — greenfield project)
**Analogs found:** 0 / 16 (no existing source code; all patterns from research)

> This is a greenfield project. No codebase analogs exist. Every pattern below is sourced
> directly from 01-RESEARCH.md with full code excerpts. The planner must use these excerpts
> as the "copy from" source rather than an existing file.

---

## File Classification

| New File | Role | Data Flow | Closest Analog | Match Quality |
|----------|------|-----------|----------------|---------------|
| `pyproject.toml` | config | — | none | no-analog |
| `requirements.txt` | config | — | none | no-analog |
| `requirements-dev.txt` | config | — | none | no-analog |
| `.python-version` | config | — | none | no-analog |
| `alembic.ini` | config | — | none | no-analog |
| `app/__init__.py` | config | — | none | no-analog |
| `app/main.py` | service (FastAPI stub) | request-response | none | no-analog |
| `app/core/config.py` | config / utility | request-response | none | no-analog |
| `app/core/hash.py` | utility | transform | none | no-analog |
| `app/core/normalise.py` | utility | transform | none | no-analog |
| `app/core/priority.py` | utility | transform | none | no-analog |
| `app/core/logging.py` | utility | — | none | no-analog |
| `app/db/models.py` | model | CRUD | none | no-analog |
| `app/db/migrations/env.py` | config | — | none | no-analog |
| `app/db/migrations/versions/001_initial_schema.py` | migration | CRUD | none | no-analog |
| `tests/unit/test_hash.py` | test | transform | none | no-analog |
| `tests/unit/test_normalise.py` | test | transform | none | no-analog |
| `tests/unit/test_priority.py` | test | transform | none | no-analog |
| `tests/unit/test_config.py` | test | request-response | none | no-analog |
| `tests/unit/test_logging.py` | test | — | none | no-analog |
| `tests/__init__.py` | config | — | none | no-analog |
| `tests/unit/__init__.py` | config | — | none | no-analog |

---

## Pattern Assignments

### `pyproject.toml` (config)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Standard Stack, Validation Architecture

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Full `pyproject.toml` must declare:
- `[project]` section: `name = "zoho-todoist-sync"`, `requires-python = ">=3.11"`
- `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, `asyncio_mode = "auto"`
- No `[tool.poetry]` — use plain pip / `requirements.txt` for Railway compatibility

---

### `requirements.txt` (config)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Standard Stack (verified versions 2026-04-23)

```
pydantic-settings==2.14.0
alembic==1.18.4
sqlalchemy[asyncio]==2.0.49
asyncpg==0.31.0
fastapi==0.136.0
uvicorn[standard]==0.46.0
arq==0.28.0
httpx==0.28.1
structlog
python-dotenv>=1.0.0
todoist-api-python==4.0.0
zohocrmsdk
resend==2.29.0
```

**Dev requirements go in `requirements-dev.txt`:**
```
pytest==9.0.2
pytest-asyncio
```

**Critical version notes from research:**
- `resend==2.29.0` — major API change from 6.9.4 cited in prior research; verify before Phase 4
- `todoist-api-python==4.0.0` — major version bump from 2.x; verify before Phase 3
- `zohocrmsdk` — assume 3.x; verify before Phase 2

---

### `.python-version` (config)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Project Constraints

```
3.12
```

Single line. Railway uses this to pin the runtime. Local dev may run 3.11 (compatible).

---

### `app/core/config.py` (config/utility, request-response)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Pattern 1: pydantic-settings Fail-Fast Config

```python
# app/core/config.py
from pydantic_settings import BaseSettings
from pydantic import field_validator

class Settings(BaseSettings):
    # Zoho OAuth (EU region)
    zoho_client_id: str
    zoho_client_secret: str
    zoho_refresh_token: str
    zoho_user_id: str
    zoho_region: str = "eu"
    zoho_todoist_task_id_field: str = ""   # resolved at startup; empty string valid default
    zoho_terminal_statuses: str = "Completed"
    zoho_job_defer_secs: int = 2

    # Todoist
    todoist_api_token: str
    todoist_project_id: str
    todoist_client_secret: str

    # Resend
    resend_api_key: str

    # Infrastructure
    database_url: str
    redis_url: str

    # App
    log_level: str = "INFO"

    @property
    def zoho_terminal_statuses_list(self) -> list[str]:
        return [s.strip() for s in self.zoho_terminal_statuses.split(",") if s.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()   # raises ValidationError on missing required var
```

**Critical rules:**
- `settings = Settings()` at module top-level — fail-fast before any request is served (INFRA-5)
- `zoho_todoist_task_id_field` defaults to `""` — Phase 2 populates it; Phase 1 only declares it
- `zoho_terminal_statuses_list` property — never use string comparison directly (EDGE-4)
- Never log the full `settings` object — secrets are present

---

### `app/core/normalise.py` (utility, transform)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Pattern 2: NormalisedTask Dataclass

```python
# app/core/normalise.py
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

FOOTER_RE = re.compile(r"\n*---\n\[zoho:\d+\]\s*$")
ZOHO_ID_RE = re.compile(r"\[zoho:(\d+)\]")   # exported for use in Phases 3+

@dataclass(frozen=True)
class NormalisedTask:
    title: str          # Unicode NFC, stripped, CRLF→LF
    due_date: str | None  # "YYYY-MM-DD" or None
    priority: int       # Todoist int 1–4
    is_completed: bool


def normalise_due_date(raw: str | None) -> str | None:
    """
    Accepts: None, "YYYY-MM-DD", "YYYY-MM-DDTHH:MM:SS+HH:MM"
    Returns: "YYYY-MM-DD" string or None.
    Uses datetime.fromisoformat() — handles tz-offset correctly on Python 3.11+.
    """
    if not raw:
        return None
    try:
        return str(datetime.fromisoformat(raw).date())
    except ValueError:
        return raw[:10]


def normalise_title(raw: str | None) -> str:
    """NFC normalise, CRLF→LF, strip leading/trailing whitespace."""
    if not raw:
        return ""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    return text.strip()


def strip_footer(description: str | None) -> str:
    """Remove the [zoho:ID] footer before hashing."""
    if not description:
        return ""
    return FOOTER_RE.sub("", description).strip()
```

**Critical rules:**
- `normalise_due_date(None) == normalise_due_date("") == None` — both must return `None`, never `""` (Pitfall 4)
- Use `datetime.fromisoformat().date()` — never `raw[:10]` (Pitfall 1)
- `unicodedata.normalize("NFC", text)` is mandatory — not just `.strip()` (Pitfall 2)
- `ZOHO_ID_RE` must be exported at module level — later phases import it
- `NormalisedTask` must have exactly 4 fields: `title`, `due_date`, `priority`, `is_completed` — no `description`, no `labels` (SYNC-7, SYNC-9)

---

### `app/core/hash.py` (utility, transform)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Pattern 2: canonical_hash()

```python
# app/core/hash.py
import hashlib
import json
from .normalise import NormalisedTask


def canonical_hash(task: NormalisedTask) -> str:
    """
    Deterministic SHA-256 hex digest of the 4 canonical sync fields.
    JSON-serialises with sorted keys for stability.
    """
    payload = {
        "title": task.title,
        "due_date": task.due_date,   # "YYYY-MM-DD" or None (serialises as null)
        "priority": task.priority,   # int 1–4
        "is_completed": task.is_completed,
    }
    serialised = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()
```

**Critical rules:**
- `sort_keys=True` — required for deterministic output regardless of dict insertion order
- `ensure_ascii=False` — preserve Unicode characters in hash input
- `due_date=None` serialises to JSON `null` — never convert to `""` (Pitfall 4)
- Only 4 keys in `payload` — adding any other field is a breaking change to all stored hashes
- Return type is hex string (64 chars for SHA-256)

---

### `app/core/priority.py` (utility, transform)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Pattern 3: Priority Mapping Table

```python
# app/core/priority.py

# Zoho priority string → Todoist priority integer
ZOHO_TO_TODOIST: dict[str, int] = {
    "Highest": 4,   # p1 / urgent (red)
    "High":    3,   # p2 (orange)
    "Normal":  2,   # p3 (blue)
    "Low":     1,   # p4 (no colour)
    "Lowest":  1,   # collapses to Low; known data loss, documented
    None:      1,   # unset priority → no priority
    "":        1,   # empty string → no priority
}

# Todoist priority integer → Zoho priority string
TODOIST_TO_ZOHO: dict[int, str] = {
    4: "Highest",
    3: "High",
    2: "Normal",
    1: "Low",     # Todoist p4 → Zoho Low (Lowest lost in round-trip)
}


def zoho_to_todoist_priority(zoho_priority: str | None) -> int:
    return ZOHO_TO_TODOIST.get(zoho_priority, 1)


def todoist_to_zoho_priority(todoist_priority: int) -> str:
    return TODOIST_TO_ZOHO.get(todoist_priority, "Low")
```

**Critical rules:**
- Todoist `priority=4` is urgent (p1/red). `priority=1` is lowest. This is counter-intuitive (Pitfall 3).
- Zoho "Highest" → 4, NOT 1. This is the critical correctness constraint from CLAUDE.md.
- `None` and `""` keys must both be present in `ZOHO_TO_TODOIST` — Zoho may return either for unset priority.
- Use `.get(key, 1)` fallback for unknown Zoho strings — do not raise, default to lowest.

---

### `app/core/logging.py` (utility)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Pattern 5: Structured Logging

```python
# app/core/logging.py
import logging
import structlog


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if level == "DEBUG" else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str):
    return structlog.get_logger(name)
```

**Usage pattern in other modules:**
```python
from app.core.logging import get_logger
log = get_logger(__name__)
log.info("startup", zoho_region=settings.zoho_region,
         todoist_task_id_field=settings.zoho_todoist_task_id_field or "NOT_SET")
```

**Critical rules:**
- Call `configure_logging(settings.log_level)` once at process startup, before any other log calls
- `DEBUG` level uses human-readable `ConsoleRenderer`; all other levels use `JSONRenderer` for Railway log aggregation (OBS-5)
- Never log `settings` object directly — it contains secrets
- `get_logger(__name__)` pattern — each module gets its own logger with its module name

---

### `app/db/models.py` (model, CRUD)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Pattern 4: Postgres Schema via Alembic

```python
# app/db/models.py — SQLAlchemy 2.x declarative style
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Index, Integer,
    String, Text, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class SyncState(Base):
    __tablename__ = "sync_state"
    zoho_task_id     = Column(String, primary_key=True)
    todoist_task_id  = Column(String, nullable=False)
    last_hash        = Column(String(64), nullable=False)   # SHA-256 hex = 64 chars
    last_synced_at   = Column(DateTime(timezone=True), nullable=False)
    zoho_last_seen   = Column(DateTime(timezone=True), nullable=True)
    orphan_check_count = Column(Integer, nullable=False, default=0)
    created_at       = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_sync_state_todoist_task_id", "todoist_task_id"),
    )


class SyncEvent(Base):
    __tablename__ = "sync_events"
    id           = Column(BigInteger, primary_key=True, autoincrement=True)
    zoho_task_id = Column(String, nullable=False)
    action       = Column(String(32), nullable=False)  # sync|echo_suppressed|overwrite|orphan|error
    source       = Column(String(32), nullable=False)  # zoho_webhook|todoist_webhook|reconciler|migration
    detail       = Column(JSONB, nullable=True)
    created_at   = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_sync_events_created_at", "created_at"),
        Index("idx_sync_events_zoho_task_id_created_at", "zoho_task_id", "created_at"),
    )


class KVStore(Base):
    __tablename__ = "kv_store"
    key        = Column(String, primary_key=True)
    value      = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                        onupdate=func.now())
```

**Critical rules:**
- Use SQLAlchemy 2.x `DeclarativeBase` — not the 1.x `declarative_base()` function
- All `DateTime` columns must use `timezone=True`
- `last_hash` is `String(64)` — SHA-256 hex digest is always exactly 64 characters
- `JSONB` for `sync_events.detail` — PostgreSQL-specific but required for efficient querying
- Required indexes: `idx_sync_state_todoist_task_id`, `idx_sync_events_created_at`, `idx_sync_events_zoho_task_id_created_at` (INFRA-2)
- `action` values: `sync`, `echo_suppressed`, `overwrite`, `orphan`, `error` — use only these strings
- `source` values: `zoho_webhook`, `todoist_webhook`, `reconciler`, `migration` — use only these strings

---

### `app/main.py` (service stub, request-response)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — State of the Art (lifespan pattern), Project Constraints

```python
# app/main.py — FastAPI stub; routes and lifespan wired in Phase 6
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging(settings.log_level)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", zoho_region=settings.zoho_region,
             todoist_task_id_field=settings.zoho_todoist_task_id_field or "NOT_SET",
             log_level=settings.log_level)
    yield
    log.info("shutdown")


app = FastAPI(lifespan=lifespan)
```

**Critical rules:**
- Use `lifespan` context manager — `on_event("startup")` is deprecated in FastAPI 0.93+ (State of the Art)
- `configure_logging()` called at module top level — before any other imports that might log
- No routes defined in Phase 1 — only the stub and lifespan

---

### `app/db/migrations/env.py` (config, Alembic)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Pattern 4, Standard Stack (alembic 1.18.4)

Standard Alembic async `env.py` pattern:
```python
# app/db/migrations/env.py
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
from app.db.models import Base
from app.core.config import settings

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    import asyncio
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**Critical rules:**
- Use `async_engine_from_config` — asyncpg driver requires async engine (not sync `engine_from_config`)
- Pull `DATABASE_URL` from `settings` — never hardcode credentials in migration files (Security)
- `target_metadata = Base.metadata` — Alembic autogenerate uses this to detect schema drift

---

### `app/db/migrations/versions/001_initial_schema.py` (migration, CRUD)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Pattern 4 (table definitions) + INFRA-2

The migration must create all three tables and all required indexes in one revision. The `upgrade()` function uses `op.create_table()` and `op.create_index()`. The `downgrade()` function drops them in reverse order.

Key columns to create (derived from models.py):

**`sync_state`**: `zoho_task_id VARCHAR PK`, `todoist_task_id VARCHAR NOT NULL`, `last_hash VARCHAR(64) NOT NULL`, `last_synced_at TIMESTAMPTZ NOT NULL`, `zoho_last_seen TIMESTAMPTZ`, `orphan_check_count INTEGER NOT NULL DEFAULT 0`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

**`sync_events`**: `id BIGSERIAL PK`, `zoho_task_id VARCHAR NOT NULL`, `action VARCHAR(32) NOT NULL`, `source VARCHAR(32) NOT NULL`, `detail JSONB`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

**`kv_store`**: `key VARCHAR PK`, `value TEXT`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`

**Required indexes** (INFRA-2):
- `idx_sync_state_todoist_task_id` on `sync_state(todoist_task_id)`
- `idx_sync_events_created_at` on `sync_events(created_at)`
- `idx_sync_events_zoho_task_id_created_at` on `sync_events(zoho_task_id, created_at)`

---

### `tests/unit/test_hash.py` (test, transform)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Code Examples: Canonical Hash Round-Trip

Full test file pattern from research (copy verbatim):

```python
# tests/unit/test_hash.py
from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title
from app.core.hash import canonical_hash
from app.core.priority import zoho_to_todoist_priority

def make_task(title="Buy milk", due="2026-05-01", priority=2, completed=False):
    return NormalisedTask(
        title=normalise_title(title),
        due_date=normalise_due_date(due),
        priority=priority,
        is_completed=completed,
    )

def test_same_logical_task_same_hash():
    t1 = make_task(due="2026-05-01T00:00:00+05:30")
    t2 = make_task(due="2026-05-01")
    assert canonical_hash(t1) == canonical_hash(t2)

def test_crlf_same_as_lf():
    t1 = make_task(title="Line one\r\nLine two")
    t2 = make_task(title="Line one\nLine two")
    assert canonical_hash(t1) == canonical_hash(t2)

def test_unicode_nfc_nfd_same_hash():
    import unicodedata
    nfc = "café"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    t1 = make_task(title=nfc)
    t2 = make_task(title=nfd)
    assert canonical_hash(t1) == canonical_hash(t2)

def test_priority_round_trip():
    from app.core.priority import zoho_to_todoist_priority, todoist_to_zoho_priority
    assert zoho_to_todoist_priority("Highest") == 4
    assert todoist_to_zoho_priority(4) == "Highest"
    assert zoho_to_todoist_priority("High") == 3
    assert todoist_to_zoho_priority(3) == "High"
    assert zoho_to_todoist_priority("Normal") == 2
    assert todoist_to_zoho_priority(2) == "Normal"
    assert zoho_to_todoist_priority("Low") == 1
    assert zoho_to_todoist_priority("Lowest") == 1
    assert zoho_to_todoist_priority(None) == 1
    assert zoho_to_todoist_priority("") == 1
    assert todoist_to_zoho_priority(1) == "Low"

def test_null_due_date_stable():
    t1 = make_task(due=None)
    t2 = make_task(due="")
    assert canonical_hash(t1) == canonical_hash(t2)

def test_label_not_in_hash():
    from dataclasses import fields
    field_names = {f.name for f in fields(NormalisedTask)}
    assert "labels" not in field_names
    assert "description" not in field_names

def test_completed_flag_changes_hash():
    t_open = make_task(completed=False)
    t_done = make_task(completed=True)
    assert canonical_hash(t_open) != canonical_hash(t_done)
```

**Test coverage map:**
- `test_same_logical_task_same_hash` → LOOP-1, LOOP-2 (due date TZ normalisation)
- `test_crlf_same_as_lf` → LOOP-2 (CRLF normalisation)
- `test_unicode_nfc_nfd_same_hash` → LOOP-2 (Unicode NFC)
- `test_priority_round_trip` → SYNC-2
- `test_null_due_date_stable` → LOOP-2, Pitfall 4
- `test_label_not_in_hash` → SYNC-7, SYNC-9
- `test_completed_flag_changes_hash` → LOOP-1

---

### `tests/unit/test_normalise.py` (test, transform)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Code Examples: Due Date Normalisation Tests

Full test file pattern from research (copy verbatim):

```python
# tests/unit/test_normalise.py
from app.core.normalise import normalise_due_date, normalise_title, strip_footer, ZOHO_ID_RE

def test_date_only_passthrough():
    assert normalise_due_date("2026-05-01") == "2026-05-01"

def test_datetime_with_tz_positive():
    assert normalise_due_date("2026-05-01T00:00:00+05:30") == "2026-05-01"

def test_datetime_with_tz_negative():
    assert normalise_due_date("2026-05-01T00:00:00-03:00") == "2026-05-01"

def test_none_due_date():
    assert normalise_due_date(None) is None

def test_empty_due_date():
    assert normalise_due_date("") is None

def test_strip_footer_basic():
    desc = "Some task\n\n---\n[zoho:1234567890]"
    assert strip_footer(desc) == "Some task"

def test_strip_footer_none():
    assert strip_footer(None) == ""

def test_strip_footer_no_footer():
    assert strip_footer("Plain description") == "Plain description"

def test_zoho_id_regex():
    desc = "Some text\n\n---\n[zoho:9876543210]"
    m = ZOHO_ID_RE.search(desc)
    assert m is not None
    assert m.group(1) == "9876543210"

def test_zoho_id_regex_no_match():
    assert ZOHO_ID_RE.search("no footer here") is None

def test_title_crlf():
    assert normalise_title("Line\r\nTwo") == "Line\nTwo"

def test_title_strip():
    assert normalise_title("  padded  ") == "padded"

def test_title_nfc():
    import unicodedata
    nfd = unicodedata.normalize("NFD", "café")
    assert normalise_title(nfd) == "café"
```

**Test coverage map:**
- `test_datetime_with_tz_positive/negative` → LOOP-2, Pitfall 1
- `test_none_due_date`, `test_empty_due_date` → LOOP-2, Pitfall 4
- `test_strip_footer_*` → SYNC-5
- `test_zoho_id_regex*` → SYNC-5 (regex export used by later phases)
- `test_title_crlf`, `test_title_strip`, `test_title_nfc` → LOOP-2, Pitfall 2

---

### `tests/unit/test_priority.py` (test, transform)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Code Examples (priority round-trip embedded in test_hash.py), Pattern 3

```python
# tests/unit/test_priority.py
from app.core.priority import (
    ZOHO_TO_TODOIST, TODOIST_TO_ZOHO,
    zoho_to_todoist_priority, todoist_to_zoho_priority
)

def test_highest_maps_to_4():
    assert zoho_to_todoist_priority("Highest") == 4

def test_high_maps_to_3():
    assert zoho_to_todoist_priority("High") == 3

def test_normal_maps_to_2():
    assert zoho_to_todoist_priority("Normal") == 2

def test_low_maps_to_1():
    assert zoho_to_todoist_priority("Low") == 1

def test_lowest_maps_to_1():
    assert zoho_to_todoist_priority("Lowest") == 1

def test_none_maps_to_1():
    assert zoho_to_todoist_priority(None) == 1

def test_empty_string_maps_to_1():
    assert zoho_to_todoist_priority("") == 1

def test_unknown_zoho_priority_maps_to_1():
    assert zoho_to_todoist_priority("SomeUnknownValue") == 1

def test_todoist_4_maps_to_highest():
    assert todoist_to_zoho_priority(4) == "Highest"

def test_todoist_3_maps_to_high():
    assert todoist_to_zoho_priority(3) == "High"

def test_todoist_2_maps_to_normal():
    assert todoist_to_zoho_priority(2) == "Normal"

def test_todoist_1_maps_to_low():
    assert todoist_to_zoho_priority(1) == "Low"

def test_round_trip_highest():
    assert todoist_to_zoho_priority(zoho_to_todoist_priority("Highest")) == "Highest"

def test_round_trip_high():
    assert todoist_to_zoho_priority(zoho_to_todoist_priority("High")) == "High"

def test_round_trip_normal():
    assert todoist_to_zoho_priority(zoho_to_todoist_priority("Normal")) == "Normal"

def test_lowest_round_trip_loses_precision():
    # Known data loss: Lowest collapses to Low in round-trip
    assert todoist_to_zoho_priority(zoho_to_todoist_priority("Lowest")) == "Low"
```

**Test coverage map:** All tests → SYNC-2

---

### `tests/unit/test_config.py` (test, request-response)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Validation Architecture (INFRA-5 test gap)

```python
# tests/unit/test_config.py
import pytest
from unittest.mock import patch
import os

def test_settings_raises_on_missing_required_var():
    """INFRA-5: Settings() must raise ValidationError when required env var is missing."""
    from pydantic_settings import BaseSettings
    from pydantic import ValidationError

    # Patch environment to remove a required var
    env_without_secret = {k: v for k, v in os.environ.items()
                          if k != "ZOHO_CLIENT_ID"}
    with patch.dict(os.environ, env_without_secret, clear=True):
        # Must re-import Settings (not settings singleton) to trigger re-validation
        with pytest.raises((ValidationError, Exception)):
            from pydantic_settings import BaseSettings
            # Re-create Settings in a clean env missing ZOHO_CLIENT_ID
            # (exact test mechanics may need adjustment based on import caching)
            pass

def test_terminal_statuses_list_single():
    """zoho_terminal_statuses_list parses single value."""
    from app.core.config import Settings
    s = Settings.model_construct(zoho_terminal_statuses="Completed")
    assert s.zoho_terminal_statuses_list == ["Completed"]

def test_terminal_statuses_list_multiple():
    """zoho_terminal_statuses_list parses comma-separated values."""
    from app.core.config import Settings
    s = Settings.model_construct(zoho_terminal_statuses="Completed,Closed,Done")
    assert s.zoho_terminal_statuses_list == ["Completed", "Closed", "Done"]

def test_terminal_statuses_list_with_spaces():
    from app.core.config import Settings
    s = Settings.model_construct(zoho_terminal_statuses="Completed, Closed , Done")
    assert s.zoho_terminal_statuses_list == ["Completed", "Closed", "Done"]
```

**Note:** The fail-fast test for missing env vars is the most important but may require careful implementation to avoid import caching issues. Use `importlib.reload` or test in a subprocess if direct patching is insufficient.

---

### `tests/unit/test_logging.py` (test)

**Analog:** none (greenfield)
**Source:** 01-RESEARCH.md — Validation Architecture (OBS-5 test gap)

```python
# tests/unit/test_logging.py
def test_configure_logging_does_not_raise():
    """OBS-5: configure_logging() must not raise for any valid level."""
    from app.core.logging import configure_logging, get_logger
    configure_logging("INFO")   # must not raise
    configure_logging("DEBUG")  # must not raise
    configure_logging("WARNING")

def test_get_logger_returns_logger():
    from app.core.logging import configure_logging, get_logger
    configure_logging("INFO")
    log = get_logger("test.module")
    assert log is not None

def test_logger_info_does_not_raise():
    from app.core.logging import configure_logging, get_logger
    configure_logging("INFO")
    log = get_logger("test.module")
    log.info("test event", key="value")  # must not raise
```

---

## Shared Patterns

### Fail-Fast Singleton Import
**Apply to:** `app/main.py`, `app/db/migrations/env.py`, and any future module that needs config
```python
from app.core.config import settings   # module-level import triggers validation on startup
```

### Structured Logging Initialization
**Apply to:** `app/main.py` (call once at startup), all future service entry points
```python
from app.core.logging import configure_logging, get_logger
configure_logging(settings.log_level)
log = get_logger(__name__)
```

### Module-Level Logger (do not call configure_logging again)
**Apply to:** All `app/` modules except entry points
```python
from app.core.logging import get_logger
log = get_logger(__name__)
```

### Test Import Pattern
**Apply to:** All test files
```python
# Import from app.core.* — never from relative paths in tests
from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title
from app.core.hash import canonical_hash
from app.core.priority import zoho_to_todoist_priority, todoist_to_zoho_priority
```

### `.env` Git Safety
**Apply to:** `.gitignore` (must be created in Phase 1)
```
.env
__pycache__/
*.pyc
.pytest_cache/
```

---

## No Analog Found

All files in this phase have no analog — this is a greenfield project. All patterns above are sourced from the research documents.

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| All 22 files listed above | various | various | Greenfield — no existing source code in the repository |

---

## Anti-Patterns (Do NOT do these)

From 01-RESEARCH.md — Anti-Patterns to Avoid:

| Anti-pattern | Correct pattern |
|---|---|
| `raw_date[:10]` for date normalisation | `datetime.fromisoformat(raw).date()` |
| `if zoho_priority == "Highest": return 4` | Use `ZOHO_TO_TODOIST.get(zoho_priority, 1)` |
| `due_date: ""` in hash payload for absent date | `due_date: None` (JSON `null`) |
| `NormalisedTask` with `description` or `labels` field | Exactly 4 fields only |
| `status == "Completed"` hardcoded | `status in settings.zoho_terminal_statuses_list` |
| `on_event("startup")` in FastAPI | `@asynccontextmanager async def lifespan(app)` |
| `pydantic.BaseSettings` | `pydantic_settings.BaseSettings` (separate package) |
| `psycopg2` sync driver | `asyncpg` with SQLAlchemy async engine |
| `declarative_base()` function (SQLAlchemy 1.x) | `class Base(DeclarativeBase): pass` (SQLAlchemy 2.x) |
| Log `settings` object | Log only safe fields: `zoho_region`, `log_level`, `todoist_task_id_field or "NOT_SET"` |

---

## Metadata

**Analog search scope:** entire `/data/home/zoho-todoist-sync/` repository
**Files scanned:** 2 (CLAUDE.md, README.md — only non-planning files present)
**Pattern source:** 01-RESEARCH.md exclusively (greenfield project)
**Pattern extraction date:** 2026-04-23
