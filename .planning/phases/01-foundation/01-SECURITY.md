---
phase: 01-foundation
security_audit_date: "2026-04-23"
asvs_level: 1
auditor: gsd-secure-phase
threats_total: 17
threats_mitigate: 11
threats_accept: 6
threats_transfer: 0
threats_closed: 17
threats_open: 0
result: SECURED
---

# Phase 01 — Foundation: Security Audit Report

**Phase:** 01 — foundation
**ASVS Level:** 1
**Audit Date:** 2026-04-23
**Result:** SECURED — all mitigate threats verified CLOSED; all accept threats documented.

---

## Threat Verification

| Threat ID | Category | Disposition | Status | Evidence |
|-----------|----------|-------------|--------|----------|
| T-1-01 | Information Disclosure | mitigate | CLOSED | `.gitignore` line 2: `.env` (exact entry); `.env.example` contains only placeholder strings (`your-zoho-client-id`, etc.) |
| T-1-02 | Information Disclosure | mitigate | CLOSED | `app/main.py` logs only `zoho_region`, `todoist_task_id_field`, `log_level` (lines 14–16); `settings` object never passed to logger anywhere |
| T-1-03 | Tampering | mitigate | CLOSED | `app/core/config.py` line 38: `settings = get_settings()` at module top-level; any missing required field raises `pydantic.ValidationError` before any import completes |
| T-1-04 | Denial of Service | accept | CLOSED | Accepted: crash on malformed env var is correct fail-fast behavior; documented in accepted risks below |
| T-1-05 | Information Disclosure | mitigate | CLOSED | `.env.example`: all secret fields use `your-*` placeholders; `DATABASE_URL` uses a localhost template (no real credentials); `TODOIST_PROJECT_ID` is a non-secret ID also documented in `CLAUDE.md` |
| T-1-06 | Tampering | mitigate | CLOSED | `app/core/hash.py` line 18: `json.dumps(payload, sort_keys=True, ensure_ascii=False)`; `app/core/normalise.py` uses `datetime.fromisoformat().date()` for TZ normalisation and `unicodedata.normalize("NFC")` for Unicode stability |
| T-1-07 | Tampering | mitigate | CLOSED | `app/core/priority.py` line 5: `"Highest": 4` (explicit, non-inverted); regression test `test_highest_is_NOT_todoist_1` confirmed in 01-02-SUMMARY.md |
| T-1-08 | Denial of Service | mitigate | CLOSED | `app/core/normalise.py` lines 26–30: `try/except ValueError` around `datetime.fromisoformat(raw).date()` returns `None` on unparseable input (WR-01 fix applied — never propagates exception to caller) |
| T-1-09 | Denial of Service | accept | CLOSED | Accepted: SHA-256 handles arbitrary-length input; Zoho/Todoist cap titles at ~500 chars |
| T-1-10 | Information Disclosure | accept | CLOSED | Accepted: task subjects are not PII per se; Railway logs are private |
| T-1-11 | Repudiation | accept | CLOSED | Accepted: sync_events audit trail deferred to Phase 3+ per plan |
| T-1-12 | Information Disclosure | mitigate | CLOSED | `app/db/migrations/env.py` line 9: `config.set_main_option("sqlalchemy.url", settings.database_url)` — runtime read, no literal URL; `001_initial_schema.py` contains only DDL, no credentials |
| T-1-13 | Information Disclosure | mitigate | CLOSED | `app/main.py` lines 14–16: logs only `zoho_region`, `todoist_task_id_field`, `log_level`; `settings` object not logged whole (negative grep confirmed in 01-03-SUMMARY.md) |
| T-1-14 | Tampering | mitigate | CLOSED | `app/db/models.py` line 17: `Column(String(64), nullable=False)`; `001_initial_schema.py` line 22: `sa.Column("last_hash", sa.String(length=64), nullable=False)` — ORM and migration agree |
| T-1-15 | Tampering | mitigate | CLOSED | Three indexes present in both ORM (`app/db/models.py`) and migration (`001_initial_schema.py`): `idx_sync_state_todoist_task_id` (models line 24, migration line 29), `idx_sync_events_created_at` (models line 38, migration line 40), `idx_sync_events_zoho_task_id_created_at` (models line 39, migration lines 41–45) |
| T-1-16 | Denial of Service | accept | CLOSED | Accepted: settings validation is synchronous and fast; lifespan startup cannot hang on it |
| T-1-17 | Elevation of Privilege | accept | CLOSED | Accepted: Railway-managed Postgres, single-tenant DB per service |

---

## Accepted Risks Log

The following threats were accepted at plan authoring time and require no mitigation code.

| Threat ID | Category | Rationale |
|-----------|----------|-----------|
| T-1-04 | Denial of Service | Crash on malformed env var is the correct fail-fast behavior. Railway healthcheck restarts with backoff. No silent degraded operation. |
| T-1-09 | Denial of Service | SHA-256 handles arbitrary-length input without overflow or crash. Zoho and Todoist both enforce title length caps (~500 chars) at the API layer. |
| T-1-10 | Information Disclosure | Task subject lines (e.g. "Call client X") are not PII under the project's data classification. Railway log streams are private and not publicly accessible. |
| T-1-11 | Repudiation | Per-hash audit trail deferred intentionally; `sync_events` table is created in this phase but event-write logic lands in Phase 3+. |
| T-1-16 | Denial of Service | `settings = get_settings()` is a synchronous call that either raises immediately or returns in < 1 ms. It cannot produce a hung lifespan. |
| T-1-17 | Elevation of Privilege | Railway provisions one Postgres instance per service in a private network. The `public` schema is the only schema in use; no multi-tenant isolation is required. |

---

## Unregistered Threat Flags

No threat flags were raised in the `## Threat Flags` sections of any SUMMARY file for this phase. All three SUMMARYs (01-01, 01-02, 01-03) explicitly state "None".

---

## Notes

- `app/core/config.py` wraps `Settings()` in `@lru_cache(maxsize=1)` as `get_settings()`, then assigns `settings = get_settings()` at module top-level. The fail-fast behavior of T-1-03 is preserved: the module-level call still executes at import time and raises `ValidationError` if any required env var is missing.
- `normalise_due_date` returns `None` (not `raw[:10]`) for unparseable dates, which is the WR-01 fix applied in 01-02. This is strictly safer than the original plan's `raw[:10]` fallback.
- The `001_initial_schema.py` migration includes a PL/pgSQL trigger for `kv_store.updated_at` not mentioned in the plan. This is an additive improvement (ensures `updated_at` is maintained even for raw SQL updates); it introduces no new security surface.
