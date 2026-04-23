# Phase 1: Foundation - Research

**Researched:** 2026-04-23
**Domain:** Python project scaffold, Postgres schema, pydantic-settings config, SHA-256 canonical hash, structured logging
**Confidence:** HIGH (Phase 1 has no external API dependencies — all correctness-critical logic is pure Python and stdlib)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INFRA-1 | Two Railway services: `web` (FastAPI) and `worker` (arq) | Project layout + pyproject.toml scaffold; no API needed in Phase 1 |
| INFRA-2 | Postgres schema: `sync_state`, `sync_events`, `kv_store` + indexes | Alembic migration SQL documented below |
| INFRA-3 | Redis on Railway (arq job queue) | Phase 1 only declares the dependency; arq worker not wired yet |
| INFRA-4 | Python 3.12, FastAPI, arq, `todoist-api-python`, Zoho SDK | requirements.txt + `.python-version` file |
| INFRA-5 | All secrets as Railway env vars; validated on startup | pydantic-settings `BaseSettings` pattern |
| INFRA-6 | Zoho OAuth EU region; proactive access token refresh | EU data-centre constants declared; refresh logic in Phase 2 |
| INFRA-7 | Startup fetch of Zoho field metadata; cache `ZOHO_TODOIST_TASK_ID_FIELD` | Settings model holds the field; Phase 2 populates it via API |
| LOOP-1 | Canonical hash loop prevention: SHA-256 of `{title, due_date, priority, is_completed}` | `canonical_hash()` pure function — fully testable without credentials |
| LOOP-2 | Normalisation rules: date→YYYY-MM-DD, priority→int 1-4, title stripped, NFC, CRLF | `normalise_task()` pure function + unit test suite |
| SYNC-2 | Field mapping + priority table (Zoho Highest→4, High→3, Normal→2, Low/unset→1) | Priority mapping table in `app/core/priority.py` |
| SYNC-5 | Todoist footer: `\n\n---\n[zoho:{ID}]`; regex `\[zoho:(\d+)\]` | Footer strip in normaliser; regex constant exported |
| SYNC-7 | Description sync OUT of v1; description excluded from hash | Hash function accepts only the 4 canonical fields |
| SYNC-9 | Labels excluded from hash | Confirmed: hash input struct has no `labels` field |
| OBS-5 | Structured logging; `LOG_LEVEL` env var; startup log of Zoho region + field cache status | `structlog` or `logging` configured at module load |
</phase_requirements>

---

## Summary

Phase 1 builds the correctness-critical foundation: project layout, Postgres schema, env-var validation, the canonical hash function with its normalisation pipeline, and structured logging. Every component produced in this phase is a pure function or a schema definition with no external API dependencies — the entire test suite must pass with `pytest` and no credentials.

The prior domain research (`.planning/research/`) is thorough and accurate for the design. This research focuses on: (1) verifying current package versions against PyPI — several versions have changed significantly from the earlier research; (2) clarifying exactly what belongs in Phase 1 vs. later phases; (3) providing the precise test matrix required by the `nyquist_validation` setting.

**Primary recommendation:** Build in this order within the phase: pyproject.toml + requirements.txt → Alembic migration → pydantic-settings config → normalisation functions → canonical hash → logging → unit tests. All five components are independently testable. Ship no API clients in Phase 1.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Postgres schema (sync_state, sync_events, kv_store) | Database / Storage | — | Pure persistence layer; no business logic |
| Env-var config validation | API / Backend (startup) | — | Fail-fast on missing secrets before accepting traffic |
| Canonical hash + normalisation | API / Backend | — | Pure functions; shared by web and worker processes |
| Structured logging | API / Backend | — | Applied in both web and worker processes |
| Priority mapping table | API / Backend | — | Shared constant; used by sync logic in later phases |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `pydantic-settings` | `2.14.0` | Env-var validation + settings model | Type-safe, fail-fast, Railway-compatible |
| `alembic` | `1.18.4` | Database migrations | Industry standard for SQLAlchemy projects |
| `sqlalchemy[asyncio]` | `2.0.49` | ORM + schema definition | Async-native, needed for later phases |
| `asyncpg` | `0.31.0` | PostgreSQL async driver | Required by SQLAlchemy async engine |
| `fastapi` | `0.136.0` | Web framework scaffold (wired in later phases) | Project requirement |
| `uvicorn[standard]` | `0.46.0` | ASGI server | Standard FastAPI server |
| `arq` | `0.28.0` | Job queue scaffold (worker wired in later phases) | Project requirement |
| `structlog` | latest stable | Structured logging | Preferred for JSON-line log output; Railway captures stdout |
| `python-dotenv` | `>=1.0.0` | `.env` loading in local dev | Development convenience only |

[VERIFIED: npm registry/PyPI] — versions confirmed via `pip3 index versions` on 2026-04-23.

### Version Notes (CRITICAL)

The prior domain research (STACK.md) cited significantly stale versions. Verified current versions:

| Package | Prior Research Said | Verified Current |
|---------|---------------------|------------------|
| `resend` | 6.9.4 | **2.29.0** — major API change; `resend.Emails.send()` API likely changed |
| `todoist-api-python` | `>=2.1.3,<3` | **4.0.0** — major version bump; API surface likely changed |
| `fastapi` | `>=0.111.0` | **0.136.0** |
| `arq` | `>=0.26.0` | **0.28.0** |
| `sqlalchemy` | `>=2.0.30` | **2.0.49** |
| `asyncpg` | `>=0.29.0` | **0.31.0** |
| `alembic` | `>=1.13.0` | **1.18.4** |
| `uvicorn` | `>=0.30.0` | **0.46.0** |
| `httpx` | `>=0.27.0` | **0.28.1** |
| `pydantic-settings` | `>=2.3.0` | **2.14.0** |

[VERIFIED: PyPI via `pip3 index versions` 2026-04-23]

**`resend` and `todoist-api-python` require explicit verification before Phase 4 (write operations).** Phase 1 does not call either — this is a heads-up for later phases.

`zohocrmsdk` could not be verified via PyPI in this environment (network restriction). [ASSUMED] latest 3.x is still correct; verify before Phase 2.

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pytest` | `9.0.2` (installed in env) | Unit tests | Entire test suite |
| `pytest-asyncio` | latest | Async test support | If any async utilities need direct testing |
| `httpx` | `0.28.1` | HTTP client (for Sync API in later phases) | Declared in Phase 1 requirements; not called yet |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `structlog` | stdlib `logging` | stdlib logging requires more boilerplate for JSON output; `structlog` gives JSON-line output natively, which Railway's log aggregator parses cleanly |
| `alembic` | plain SQL migration file | Alembic is more setup work but enables incremental migrations in later phases; plain SQL is simpler but requires manual tracking |

**Installation:**
```bash
pip install pydantic-settings==2.14.0 alembic==1.18.4 "sqlalchemy[asyncio]==2.0.49" asyncpg==0.31.0 fastapi==0.136.0 "uvicorn[standard]==0.46.0" arq==0.28.0 httpx==0.28.1 structlog python-dotenv pytest pytest-asyncio
```

---

## Architecture Patterns

### System Architecture Diagram

Phase 1 produces no runtime data flow. The diagram shows the module layout that later phases extend.

```
app/
 ├── core/
 │    ├── config.py        ← Settings (pydantic-settings); fail-fast on startup
 │    ├── hash.py          ← canonical_hash(task) → str (SHA-256 hex)
 │    ├── normalise.py     ← normalise_task(raw, source) → NormalisedTask
 │    ├── priority.py      ← ZOHO_TO_TODOIST, TODOIST_TO_ZOHO mapping tables
 │    └── logging.py       ← configure_logging(level) — structlog setup
 ├── db/
 │    ├── models.py        ← SQLAlchemy declarative models (SyncState, SyncEvent, KVStore)
 │    └── migrations/      ← Alembic env.py + versions/
 └── main.py               ← FastAPI app stub (lifespan wired in Phase 6)

tests/
 └── unit/
      ├── test_hash.py
      ├── test_normalise.py
      └── test_priority.py
```

### Recommended Project Structure

```
zoho-todoist-sync/
├── app/
│   ├── core/
│   │   ├── config.py       # pydantic-settings Settings class
│   │   ├── hash.py         # canonical_hash() pure function
│   │   ├── normalise.py    # normalise_task() + NormalisedTask dataclass
│   │   ├── priority.py     # bidirectional priority mapping tables
│   │   └── logging.py      # structlog configuration
│   ├── db/
│   │   ├── models.py       # SQLAlchemy ORM models
│   │   └── migrations/     # Alembic migration scripts
│   │       ├── env.py
│   │       └── versions/
│   │           └── 001_initial_schema.py
│   └── main.py             # FastAPI app (stub in Phase 1)
├── tests/
│   └── unit/
│       ├── test_hash.py
│       ├── test_normalise.py
│       └── test_priority.py
├── alembic.ini
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
└── .python-version         # contains "3.12"
```

### Pattern 1: pydantic-settings Fail-Fast Config

**What:** Declare all required env vars in a `BaseSettings` subclass. Import `settings` at module top-level so any missing var raises `ValidationError` on process startup before serving any requests.

**When to use:** Always. This is the fail-fast pattern required by INFRA-5.

```python
# app/core/config.py
# Source: [CITED: docs.pydantic.dev/latest/concepts/pydantic_settings/]
from pydantic_settings import BaseSettings
from pydantic import field_validator

class Settings(BaseSettings):
    # Zoho OAuth (EU region)
    zoho_client_id: str
    zoho_client_secret: str
    zoho_refresh_token: str
    zoho_user_id: str
    zoho_region: str = "eu"
    zoho_todoist_task_id_field: str = ""   # resolved at startup; empty string is valid default
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

### Pattern 2: NormalisedTask Dataclass + canonical_hash()

**What:** A dataclass holding exactly the 4 fields that participate in the canonical hash. Normalisation is a separate step from hashing, which enables unit testing of each independently.

**When to use:** Any time a task payload arrives from either Zoho or Todoist. Normalise first, hash second.

```python
# app/core/normalise.py
# Source: [ASSUMED] — standard Python stdlib + unicodedata
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
        # fromisoformat handles both date-only and datetime-with-tz
        return str(datetime.fromisoformat(raw).date())
    except ValueError:
        # Fallback: take first 10 chars (safe for YYYY-MM-DD substring)
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

```python
# app/core/hash.py
# Source: [ASSUMED] — standard hashlib
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
        "due_date": task.due_date,   # "YYYY-MM-DD" or None
        "priority": task.priority,   # int 1–4
        "is_completed": task.is_completed,
    }
    serialised = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()
```

### Pattern 3: Priority Mapping Table

**What:** Explicit bidirectional dict, not ad-hoc if/elif chains. Zoho → Todoist and Todoist → Zoho are separate dicts to make round-trip tests obvious.

**Critical correctness note:** Todoist `priority=4` is urgent (p1/red). Todoist `priority=1` is no priority. This is counter-intuitive and was confirmed wrong in a prior Make.com integration.

```python
# app/core/priority.py
# Source: [CITED: developer.todoist.com/rest/v2/#tasks] + [VERIFIED: FEATURES.md research]

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
    1: "Low",     # Todoist p4 → Zoho Low (Lowest is lost in round-trip)
}


def zoho_to_todoist_priority(zoho_priority: str | None) -> int:
    return ZOHO_TO_TODOIST.get(zoho_priority, 1)


def todoist_to_zoho_priority(todoist_priority: int) -> str:
    return TODOIST_TO_ZOHO.get(todoist_priority, "Low")
```

### Pattern 4: Postgres Schema via Alembic

**What:** Three tables with required indexes. Migration applies cleanly from scratch via `alembic upgrade head`.

```python
# app/db/models.py — SQLAlchemy 2.x declarative style
# Source: [CITED: docs.sqlalchemy.org/en/20/orm/mapping_styles.html]
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

### Pattern 5: Structured Logging

**What:** `structlog` configured once at application entry point. `LOG_LEVEL` env var controls verbosity.

```python
# app/core/logging.py
# Source: [CITED: www.structlog.org/en/stable/getting-started.html]
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

Startup log example (Phase 2 fills in the actual field name and status):

```python
log = get_logger(__name__)
log.info("startup", zoho_region=settings.zoho_region,
         todoist_task_id_field=settings.zoho_todoist_task_id_field or "NOT_SET")
```

### Anti-Patterns to Avoid

- **Including description in the canonical hash:** Description is OUT of v1 sync scope (SYNC-7). The hash struct must have exactly 4 fields.
- **Including labels in the canonical hash:** Labels are local to Todoist and must never trigger a sync write (SYNC-9).
- **String-slicing the due date:** `raw_date[:10]` works for `YYYY-MM-DDTHH:MM:SS` but fails for `YYYY-MM-DDTHH:MM:SS+05:30` — use `datetime.fromisoformat().date()` instead.
- **Using `None` directly in JSON hash payload:** `json.dumps({"due_date": None})` produces `'null'` which is fine; do not convert to `""` — keep as `None` so the JSON representation is stable.
- **Ad-hoc priority mapping:** Any `if zoho_priority == "Highest": return 4` pattern must be replaced by the mapping table to ensure testability and completeness.
- **Hardcoding terminal statuses:** `status == "Completed"` must be replaced by `status in settings.zoho_terminal_statuses_list`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Env-var validation | Manual `os.environ.get()` with `if not val: sys.exit(1)` | `pydantic-settings BaseSettings` | Type coercion, nested models, `.env` support, clear error messages |
| Database migrations | Hand-written SQL + version tracking | `alembic` | Incremental versioning, `upgrade head` idempotency, environment-aware |
| Structured log formatting | Custom log formatter | `structlog` | JSON-line output, context binding, Railway-compatible |
| SHA-256 hashing | Any custom digest | `hashlib.sha256` (stdlib) | No dependency, no edge cases, FIPS-compatible |
| Unicode normalisation | `str.lower()` / `str.strip()` alone | `unicodedata.normalize("NFC", s)` | NFC/NFD diacritic variants produce same bytes after NFC |

**Key insight:** Phase 1's entire value is in correct, tested normalisation. Every hour spent on custom utilities instead of testing edge cases is a future infinite-loop bug.

---

## Common Pitfalls

### Pitfall 1: Due Date Timezone Shift

**What goes wrong:** `"2026-05-01T00:00:00+05:30"` sliced as `[:10]` gives `"2026-05-01"` — correct by luck. But `"2026-05-01T23:00:00-03:00"` sliced gives `"2026-05-01"` while the UTC equivalent is `"2026-05-02"`. The Zoho API returns date-only strings for the Tasks module, but may include timezone offset from the org's setting. Always use the date-aware parser.

**Why it happens:** String slicing ignores timezone context. The issue only manifests for orgs west of UTC when the local midnight crosses the UTC date boundary.

**How to avoid:** `datetime.fromisoformat(raw).date()` is correct. Verified: `datetime.fromisoformat("2026-05-01T00:00:00+05:30").date()` returns `datetime.date(2026, 5, 1)` — tested above.

**Warning signs:** Due dates appear one day ahead in Todoist compared to Zoho UI display.

### Pitfall 2: Unicode NFC/NFD Hash Mismatch

**What goes wrong:** `"café"` can be encoded as U+0063 U+0061 U+0066 U+00E9 (NFC, single precomposed) or U+0063 U+0061 U+0066 U+0065 U+0301 (NFD, base + combining accent). `hashlib` hashes bytes — NFD bytes differ from NFC bytes. Zoho and Todoist may return the same string in different forms depending on internal storage.

**Why it happens:** Unicode has multiple canonical representations that are visually identical but bytewise different.

**How to avoid:** `unicodedata.normalize("NFC", text)` before hashing. Apply to `title` field (the only string in the hash payload — description is excluded).

**Warning signs:** Hashes mismatch for tasks with accented characters; `echo_suppressed` count is lower than expected for European language content.

### Pitfall 3: Priority Mapping Inversion

**What goes wrong:** Todoist `priority=1` is the lowest (no-colour, p4), not urgent. `priority=4` is urgent (red, p1). "Highest" → 1 would map the most urgent Zoho task to the least urgent Todoist priority. This is the exact bug in the prior Make.com integration.

**Why it happens:** Naming convention: Todoist displays "p1" but stores integer `4`. The number and the priority label are inverted.

**How to avoid:** Use the explicit `ZOHO_TO_TODOIST` dict. Unit test round-trip: `zoho("Highest") == 4` and `todoist(4) == "Highest"`.

**Warning signs:** Zoho "Highest" tasks appear with no colour in Todoist instead of red.

### Pitfall 4: `None` vs `""` in Hash Payload

**What goes wrong:** After first sync, a task with `due_date=None` in Zoho becomes a Todoist task with `due=null`. On the next reconciliation, Zoho still returns `None` for `Due_Date`. Todoist may return `null` for the `due` field object (not `{"date": null}`) or the entire `due` field may be absent. If `None` and `""` are normalised differently, hashes diverge and the echo is not suppressed.

**How to avoid:** In `normalise_due_date`, return `None` (Python `None`, which JSON-serialises to `null`) for absent due dates. Do not convert to `""`. Be consistent: `normalise_due_date(None) == normalise_due_date("") == None`.

### Pitfall 5: `zoho_todoist_task_id_field` Field Name Assumption

**What goes wrong:** Hardcoding `"Todoist_Task_ID"` as the Zoho custom field API name. Zoho may generate the name with a suffix (`__c`), a slightly different case, or a numeric suffix if a field with the same display name was deleted and recreated.

**How to avoid:** INFRA-7 requires fetching the field name at startup from `GET /crm/v6/settings/fields?module=Tasks`. In Phase 1, only declare the `zoho_todoist_task_id_field` settings key with a default of `""`. Phase 2 populates it via API call and logs the resolved value. Do not use the field in Phase 1 code.

---

## Code Examples

### Canonical Hash Round-Trip (test pattern)

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
    t1 = make_task(due="2026-05-01T00:00:00+05:30")  # Zoho tz variant
    t2 = make_task(due="2026-05-01")                  # date-only
    assert canonical_hash(t1) == canonical_hash(t2)

def test_footer_stripped_same_hash():
    t_with_footer = make_task(title="Task\n\n---\n[zoho:12345]")
    t_without     = make_task(title="Task")
    # title does NOT include description; footer is in description, not title
    # This test verifies description is excluded from hash payload
    assert canonical_hash(t_with_footer) != canonical_hash(t_without)
    # ^ This test is intentionally checking that title changes affect hash;
    # The footer-stripping test belongs in test_normalise.py for strip_footer()

def test_crlf_same_as_lf():
    t1 = make_task(title="Line one\r\nLine two")
    t2 = make_task(title="Line one\nLine two")
    assert canonical_hash(t1) == canonical_hash(t2)

def test_unicode_nfc_nfd_same_hash():
    import unicodedata
    nfc = "café"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd   # byte-level different
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
    assert canonical_hash(t1) == canonical_hash(t2)  # both normalise to None

def test_label_not_in_hash():
    # Labels are not part of NormalisedTask; this verifies by construction
    from dataclasses import fields
    field_names = {f.name for f in fields(NormalisedTask)}
    assert "labels" not in field_names
    assert "description" not in field_names

def test_completed_flag_changes_hash():
    t_open = make_task(completed=False)
    t_done = make_task(completed=True)
    assert canonical_hash(t_open) != canonical_hash(t_done)
```

### Due Date Normalisation Tests

```python
# tests/unit/test_normalise.py
from app.core.normalise import normalise_due_date, normalise_title, strip_footer, ZOHO_ID_RE

def test_date_only_passthrough():
    assert normalise_due_date("2026-05-01") == "2026-05-01"

def test_datetime_with_tz_positive():
    assert normalise_due_date("2026-05-01T00:00:00+05:30") == "2026-05-01"

def test_datetime_with_tz_negative():
    # Western timezone, midnight local — date must not shift
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
    assert normalise_title(nfd) == "café"  # normalised to NFC
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `on_event("startup")` in FastAPI | `lifespan` context manager | FastAPI 0.93+ | `on_event` is deprecated; use `lifespan` |
| `pydantic.BaseSettings` | `pydantic_settings.BaseSettings` | pydantic v2 | Settings moved to separate package |
| SQLAlchemy 1.x `Session` | SQLAlchemy 2.x `async_sessionmaker` | 2022 | Async-native session management |
| `resend==6.9.4` (cited in prior research) | `resend==2.29.0` (PyPI current) | Unknown | Major version; API may differ — verify before Phase 4 |
| `todoist-api-python<3` (prior research) | `todoist-api-python==4.0.0` (current) | 2025 | Major version; verify API changes before Phase 3 |

**Deprecated/outdated:**
- `FileStore` for Zoho SDK token persistence: Railway filesystem is ephemeral; use Postgres custom `TokenStore`.
- `zcrmsdk` PyPI package: abandoned; use `zohocrmsdk`.
- `psycopg2`: synchronous only; use `asyncpg` with SQLAlchemy async engine.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `zohocrmsdk` latest version is still 3.x | Standard Stack | Phase 2 code may need adjustment if SDK has a major version bump |
| A2 | `resend==2.29.0` is a major version change from 6.9.4 cited in prior research (the versioning appears to have been reset) | Standard Stack — Version Notes | Phase 4 email sending code will fail if `resend.Emails.send()` interface changed |
| A3 | `todoist-api-python==4.0.0` has a different API surface from 2.x | Standard Stack — Version Notes | Phase 3/4 Todoist client code needs verification against v4 docs |
| A4 | `structlog` latest stable has the same `configure()` interface as documented | Pattern 5 | Logging setup will fail with import/attribute errors on startup |
| A5 | arq 0.28.0 `cron()` helper supports `"*/15 * * * *"` syntax | Standard Stack | Phase 7 reconciliation cron will need a workaround |

---

## Open Questions

1. **`todoist-api-python` 4.0.0 API surface**
   - What we know: version 4.0.0 is current on PyPI (major version bump from 2.x)
   - What's unclear: whether `TodoistAPI`, `api.get_task()`, `api.add_task()` etc. still have the same signatures
   - Recommendation: Before Phase 3, run `pip show todoist-api-python` and check changelog

2. **`resend` 2.29.0 email API**
   - What we know: prior research cited 6.9.4; current is 2.29.0 — this is a version reset or package renaming
   - What's unclear: whether `resend.Emails.send({"from": ..., "to": ..., ...})` still works
   - Recommendation: Check `https://resend.com/docs/send-with-python` before Phase 4

3. **arq 0.28.0 cron syntax**
   - What we know: Open question from REQUIREMENTS.md; arq 0.28.0 is current
   - What's unclear: whether `cron(fn, minute={0, 15, 30, 45})` or `cron("*/15 * * * *")` syntax is supported
   - Recommendation: Read arq changelog for 0.27+ before Phase 7

4. **`zohocrmsdk` exact current version**
   - What we know: PyPI was unreachable from this environment; prior research says 3.x
   - What's unclear: whether a 4.x version exists with breaking changes
   - Recommendation: Verify with `pip show zohocrmsdk` or `curl https://pypi.org/pypi/zohocrmsdk/json` before Phase 2

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | Runtime (target) | Dev env has 3.11.2 | 3.11.2 (dev) / 3.12 (Railway) | `.python-version` pins 3.12 for Railway |
| pytest | Test suite | ✓ | 9.0.2 | — |
| PostgreSQL | Schema + migrations | Not on dev machine | — | Use Railway DB URL in test; or SQLite for unit tests that don't hit DB |
| Redis | arq worker | Not on dev machine | — | Phase 1 has no Redis calls; not a blocker |

**Missing dependencies with no fallback for Phase 1:**
- None. Phase 1 is pure Python — no external services are called.

**Note on Python version:** Dev environment has 3.11.2; target Railway has 3.12. The code must be compatible with 3.11+ to run locally. The specific feature used — `datetime.fromisoformat()` with timezone offsets — works on 3.11+ (confirmed by local test). No 3.12-only features should be used in Phase 1.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` section |
| Quick run command | `pytest tests/unit/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| LOOP-1 | `canonical_hash()` returns same hex for same logical task | unit | `pytest tests/unit/test_hash.py -x` | ❌ Wave 0 |
| LOOP-2 | Due date normalises `YYYY-MM-DDTHH:MM:SS+HH:MM` → `YYYY-MM-DD` | unit | `pytest tests/unit/test_normalise.py::test_datetime_with_tz_positive -x` | ❌ Wave 0 |
| LOOP-2 | CRLF normalised to LF before hashing | unit | `pytest tests/unit/test_normalise.py::test_title_crlf -x` | ❌ Wave 0 |
| LOOP-2 | Unicode NFC normalisation before hashing | unit | `pytest tests/unit/test_hash.py::test_unicode_nfc_nfd_same_hash -x` | ❌ Wave 0 |
| LOOP-2 | `None` and `""` due dates both map to `None` | unit | `pytest tests/unit/test_normalise.py::test_empty_due_date -x` | ❌ Wave 0 |
| SYNC-2 | Priority mapping Zoho Highest → Todoist 4 (not 1) | unit | `pytest tests/unit/test_priority.py::test_priority_round_trip -x` | ❌ Wave 0 |
| SYNC-2 | Priority round-trip: Zoho Highest → 4 → Zoho Highest | unit | `pytest tests/unit/test_priority.py::test_priority_round_trip -x` | ❌ Wave 0 |
| SYNC-5 | Footer `\n\n---\n[zoho:ID]` stripped before hashing | unit | `pytest tests/unit/test_normalise.py::test_strip_footer_basic -x` | ❌ Wave 0 |
| SYNC-7 | Description excluded from `NormalisedTask` dataclass | unit | `pytest tests/unit/test_hash.py::test_label_not_in_hash -x` | ❌ Wave 0 |
| SYNC-9 | Labels field absent from `NormalisedTask` | unit | `pytest tests/unit/test_hash.py::test_label_not_in_hash -x` | ❌ Wave 0 |
| INFRA-5 | `settings = Settings()` raises on missing required var | unit | `pytest tests/unit/test_config.py -x` | ❌ Wave 0 |
| OBS-5 | `configure_logging(level)` does not raise; log_level from settings | unit | `pytest tests/unit/test_logging.py -x` | ❌ Wave 0 |
| INFRA-2 | Alembic migration applies cleanly from scratch | integration | Manual: `alembic upgrade head` against Railway DB URL | ❌ Wave 0 (file needed) |

### Sampling Rate

- **Per task commit:** `pytest tests/unit/ -x -q`
- **Per wave merge:** `pytest tests/unit/ -v`
- **Phase gate:** All unit tests green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_hash.py` — covers LOOP-1, LOOP-2 (Unicode, CRLF, completed flag)
- [ ] `tests/unit/test_normalise.py` — covers LOOP-2 (date, footer, strip)
- [ ] `tests/unit/test_priority.py` — covers SYNC-2 (mapping table, round-trip)
- [ ] `tests/unit/test_config.py` — covers INFRA-5 (fail-fast on missing var)
- [ ] `tests/unit/test_logging.py` — covers OBS-5 (logging setup)
- [ ] `tests/__init__.py`, `tests/unit/__init__.py` — package init files
- [ ] `pyproject.toml` with `[tool.pytest.ini_options]` section
- [ ] `alembic.ini` + `app/db/migrations/env.py` — Alembic scaffold
- [ ] `app/db/migrations/versions/001_initial_schema.py` — initial migration

---

## Security Domain

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No (Phase 1 has no auth logic) | — |
| V3 Session Management | No | — |
| V4 Access Control | No | — |
| V5 Input Validation | Yes — env-var validation | `pydantic-settings BaseSettings` |
| V6 Cryptography | Yes — SHA-256 canonical hash | `hashlib.sha256` (stdlib, FIPS-compatible) |

Phase 1 has minimal security surface. Key controls:

- `.env` file must be in `.gitignore` from day 1 — it will hold real credentials in local dev.
- `settings` object must never be logged in full; log only safe fields (region, log_level, non-secret fields).
- Alembic migration must not include credentials in the generated migration files.

---

## Sources

### Primary (HIGH confidence)

- PyPI `pip3 index versions` output — verified package versions 2026-04-23
- Python 3.11 stdlib test — `datetime.fromisoformat("2026-05-01T00:00:00+05:30").date()` returns correct value
- `.planning/research/STACK.md` — comprehensive prior research on this project's stack
- `.planning/research/ARCHITECTURE.md` — canonical hash design and schema review
- `.planning/research/PITFALLS.md` — normalisation edge cases (S1) are directly applicable to Phase 1
- `.planning/research/FEATURES.md` — priority mapping table and field names

### Secondary (MEDIUM confidence)

- `.planning/REQUIREMENTS.md` — authoritative requirement definitions
- `.planning/ROADMAP.md` — phase boundary definitions
- `CLAUDE.md` — stack decisions locked by project

### Tertiary (LOW confidence)

- arq 0.28.0 cron syntax — assumed compatible with prior documentation; needs verification before Phase 7

---

## Project Constraints (from CLAUDE.md)

| Directive | How It Applies to Phase 1 |
|-----------|--------------------------|
| Python 3.12 | Pin in `.python-version`; write 3.11+ compatible code for local dev |
| FastAPI | Scaffold `app/main.py` with `lifespan` pattern; no routes needed in Phase 1 |
| arq | Declare `WorkerSettings` stub; no functions wired yet |
| `todoist-api-python` | Listed in requirements.txt; not called in Phase 1 |
| Zoho official Python SDK (`zohocrmsdk`) | Listed in requirements.txt; not called in Phase 1 |
| Resend for email notifications | Listed in requirements.txt; not called in Phase 1 |
| Postgres + Redis on Railway | Schema in Alembic migration; connection strings in Settings |
| All secrets as env vars | Enforced by `pydantic-settings BaseSettings` |
| Zoho org region EU | `zoho_region: str = "eu"` default in Settings |
| Todoist project ID `6gCPcWwM392GhXQh` | Hardcoded default in Settings |
| Priority: NOT inverted (Highest→4) | Enforced by `ZOHO_TO_TODOIST` dict + round-trip unit tests |
| Due date always date-only | Enforced by `normalise_due_date()` + unit tests |
| Description sync OUT of v1 | `NormalisedTask` has no `description` field |
| Loop prevention via canonical hash | `canonical_hash(NormalisedTask)` is the primary Phase 1 deliverable |

---

## Metadata

**Confidence breakdown:**
- Standard stack (versions): HIGH — verified via PyPI on 2026-04-23
- Architecture: HIGH — pure Python, no external dependencies; patterns are standard
- Pitfalls: HIGH — inherited from thorough prior research + local stdlib test
- Normalisation correctness: HIGH — stdlib `datetime.fromisoformat()` verified locally

**Research date:** 2026-04-23
**Valid until:** 2026-05-23 (stable stdlib; package versions should be re-verified if > 30 days elapse before implementation)
