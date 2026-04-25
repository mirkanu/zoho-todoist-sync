# Phase 8: Observability & Migration - Context

**Gathered:** 2026-04-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 8 delivers: the `/health` endpoint, a daily arq cron that creates a summary Todoist task and purges old sync_events, an E2E test script that verifies live round-trip sync before migration, and a migration script that links all existing Make.com task pairs into the new sync system without touching un-linked tasks destructively.

</domain>

<decisions>
## Implementation Decisions

### Migration Script

- **D-01:** Script lives at `scripts/migrate.py` — standalone file in a new `scripts/` directory, invoked via `python scripts/migrate.py`. Run as a Railway one-off command or locally with env vars.
- **D-02:** `--dry-run` flag required. Prints what it would do (N tasks linked, M created, P descriptions updated) without writing anything. Migration must be previewed before touching live data.
- **D-03:** If Zoho has a `Todoist_Task_ID` that no longer exists in Todoist (404): log a warning, treat as if the field were empty (create a new Todoist task with the `[zoho:ID]` footer, write the new Todoist ID back to Zoho, store in `sync_state`). Do not abort the whole migration for one missing task.

### E2E Test

- **D-04:** Script lives at `scripts/e2e_test.py` — standalone alongside `migrate.py`. Not part of the pytest suite; invoked manually before running migration.
- **D-05:** Test creates a real Zoho task via the Zoho API at the start, runs all assertions, then deletes it from both systems at the end. Fully self-contained — no permanent test artifacts in the live account.
- **D-06:** Propagation verification uses polling: check Todoist API every 5s for up to 90s. Pass if task appears within timeout; fail with a clear error message if it does not. This validates the real webhook → Redis → worker path.

### Daily Summary Task

- **D-07:** After creating the `Sync summary: {date}` Todoist task, immediately complete it via the complete API. Acts as a searchable log entry in completed tasks without cluttering the active task list.
- **D-08:** Content is counts only, exactly as OBS-3: `{N} syncs, {M} errors, {P} echoes suppressed`. No extra fields.
- **D-09:** The 90-day `sync_events` cleanup (OBS-4) runs in the same daily cron function as the summary task creation — cleanup first, then create the summary so the counts reflect the post-cleanup state of the data.

### Health Endpoint

- **D-10:** No thresholds discussed (user skipped this area) — Claude's discretion. Suggested defaults: `error` if queue has failed jobs > 0 OR reconciler_last_run is more than 30 min stale; `degraded` if errors_24h > 10; `ok` otherwise. All data must come from DB/kv_store only (no live API calls) to meet the 100ms SLA.

### Claude's Discretion

- Health status thresholds (ok/degraded/error) — user did not discuss; defaults suggested in D-10
- Health endpoint router placement — can live in `app/webhooks/router.py` or a new `app/health/router.py`; either is fine
- Whether `scripts/` uses `asyncio.run()` or a sync wrapper for async DB/API calls

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above and in REQUIREMENTS.md.

### Requirements (read these sections)
- `.planning/REQUIREMENTS.md` §OBS-1 — health endpoint shape and HTTP status rules
- `.planning/REQUIREMENTS.md` §OBS-2 — sync_events schema (action, source, detail values)
- `.planning/REQUIREMENTS.md` §OBS-3 — daily summary task title/content format
- `.planning/REQUIREMENTS.md` §OBS-4 — 90-day cleanup
- `.planning/REQUIREMENTS.md` §SEED-1 — migration is NOT a fresh seed (existing pairs must be linked)
- `.planning/REQUIREMENTS.md` §SEED-2 — migration algorithm (fetch Zoho tasks → link or create → store sync_state)
- `.planning/REQUIREMENTS.md` §SEED-3 — description migration: replace Make.com preamble with `[zoho:ID]` footer only
- `.planning/REQUIREMENTS.md` §SEED-4 — E2E test must complete before migration runs against live data

### Existing code to read
- `app/db/models.py` — SyncState, SyncEvent, KVStore schemas
- `app/worker/settings.py` — cron job registration pattern (daily cron joins existing reconcile/orphan crons)
- `app/worker/reconciler.py` — KV key names (`reconciler_last_run`, `orphan_sweep_last_run`) used by health endpoint
- `app/zoho/writer.py` — `write_todoist_id_to_zoho` for migration's write-back step
- `app/main.py` — lifespan and router registration pattern

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `app/db/models.py:SyncEvent` — direct SQL count queries (`action='sync'`, `action='error'`, `action='echo_suppressed'`, `created_at > now()-24h`) power the health endpoint and daily summary
- `app/db/models.py:KVStore` — `reconciler_last_run` and `orphan_sweep_last_run` keys already written by Phase 7; health endpoint reads these
- `app/db/models.py:SyncState` — `COUNT(*)` gives `active_tasks` for health response
- `app/zoho/client.py:ZohoClient` — migration script reuses this for fetching all open tasks and writing back Todoist IDs
- `app/todoist/client.py:TodoistClient` — migration script reuses this for fetching/creating/updating Todoist tasks
- `app/core/hash.py:canonical_hash` — migration script uses this to compute `last_hash` when seeding `sync_state`
- `app/worker/reconciler.py` — pattern for async arq cron function; daily summary follows same structure
- `app/core/notifications.py` — Resend pattern already established; migration/E2E don't need it

### Established Patterns
- arq cron registration in `WorkerSettings.cron_jobs` (app/worker/settings.py) — daily cron uses `cron(daily_summary, minute={0}, hour={0}, second=0, timeout=120)`
- `async with session_factory() as session:` pattern for all DB access
- `upsert_kv(session, key, value)` for writing KV keys
- Structured logging via `get_logger(__name__)` — migration and E2E scripts should use this too

### Integration Points
- Health endpoint: mount at `/health` on the FastAPI app in `app/main.py` (or via new router)
- Daily summary cron: add to `WorkerSettings.cron_jobs` in `app/worker/settings.py`
- Migration script: standalone `scripts/migrate.py` — uses `asyncio.run()` entry point with its own engine/session setup (mirrors `on_startup` pattern from `app/worker/settings.py`)
- E2E script: standalone `scripts/e2e_test.py` — same async entry point pattern

</code_context>

<specifics>
## Specific Ideas

- Migration script must replace the Make.com preamble entirely (not append). SEED-3 is explicit: existing description content is discarded, only the `\n\n---\n[zoho:{ZOHO_ID}]` footer remains.
- E2E test should assert no duplicate rows in `sync_events` for the test task (i.e., the echo-suppression path worked — no infinite loop).
- Daily summary task is created in the same Todoist project as synced tasks (`TODOIST_PROJECT_ID`).

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 08-observability-migration*
*Context gathered: 2026-04-25*
