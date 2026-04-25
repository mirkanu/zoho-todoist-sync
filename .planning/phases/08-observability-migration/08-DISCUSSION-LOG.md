# Phase 8: Observability & Migration - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-25
**Phase:** 08-observability-migration
**Areas discussed:** Migration script design, E2E test approach, Daily summary task

---

## Migration Script Design

| Option | Description | Selected |
|--------|-------------|----------|
| scripts/migrate.py | Standalone script in new scripts/ directory; run via `python scripts/migrate.py` | ✓ |
| python -m app.migrate | Module entry point inside app package | |
| CLI via typer/click | Full CLI with subcommands | |

**User's choice:** `scripts/migrate.py` — standalone script

---

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, --dry-run | Prints what it would do without writing; essential safety net | ✓ |
| No dry-run | Simpler; E2E test is the safety gate anyway | |

**User's choice:** Yes, `--dry-run` flag required

---

| Option | Description | Selected |
|--------|-------------|----------|
| Log warning, create new Todoist task | Treat missing Todoist task as if field were empty; create new, write back ID | ✓ |
| Log error, skip | Log as problem, skip — leave for manual resolution | |
| Abort migration | Stop entire run | |

**User's choice:** Log warning, create new Todoist task

---

## E2E Test Approach

| Option | Description | Selected |
|--------|-------------|----------|
| Create via API, delete after | Creates real Zoho task, runs assertions, deletes from both systems | ✓ |
| Designated permanent test task | Fixed Zoho task ID committed to config | |
| Manual: user pre-creates task | Docs tell user to create manually, script takes ID as argument | |

**User's choice:** Create via API, delete after

---

| Option | Description | Selected |
|--------|-------------|----------|
| scripts/e2e_test.py | Standalone script alongside migrate.py; invoked manually | ✓ |
| tests/e2e/test_e2e.py | Inside pytest suite with @pytest.mark.e2e | |

**User's choice:** `scripts/e2e_test.py`

---

| Option | Description | Selected |
|--------|-------------|----------|
| Poll Todoist API with timeout | Check every 5s for up to 90s; validates real webhook→Redis→worker path | ✓ |
| Invoke arq job directly | Faster but bypasses the real path | |

**User's choice:** Poll Todoist API with timeout (5s interval, 90s max)

---

## Daily Summary Task

| Option | Description | Selected |
|--------|-------------|----------|
| Complete immediately | Create then close via complete API; acts as log entry | ✓ |
| Leave open | Stays in active task list as daily reminder | |

**User's choice:** Complete immediately after creation

---

| Option | Description | Selected |
|--------|-------------|----------|
| Counts only | Exactly as OBS-3: `{N} syncs, {M} errors, {P} echoes suppressed` | ✓ |
| Counts + last sync time | Add `Last sync: HH:MM UTC` from kv_store | |
| Counts + error details | Append last 3 error messages | |

**User's choice:** Counts only (OBS-3 as specified)

---

## Claude's Discretion

- Health status thresholds (ok/degraded/error) — user skipped; Claude to define sensible defaults
- Health endpoint router placement
- Async entry point pattern for standalone scripts

## Deferred Ideas

None.
