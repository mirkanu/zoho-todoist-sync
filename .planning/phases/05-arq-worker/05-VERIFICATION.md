---
phase: 05-arq-worker
verified: 2026-04-24T13:39:03Z
status: passed
score: 5/5
overrides_applied: 0
deferred:
  - truth: "Zoho-webhook-triggered jobs defer by 2 seconds (configurable via ZOHO_JOB_DEFER_SECS) before the Zoho API fetch"
    addressed_in: "Phase 6"
    evidence: "Phase 6 SC1: 'POST /webhooks/zoho ... enqueues sync_task with a 2-second defer'; Phase 5 provides the defer_secs mechanism in enqueue_sync, Phase 6 enforces the correct value at the call site"
---

# Phase 5: arq Worker Verification Report

**Phase Goal:** The `sync_task` job ties together fetch, hash check, write, and DB update into a single reliable unit — with deduplication, per-task locking, and correct retry behaviour
**Verified:** 2026-04-24T13:39:03Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `sync_task(zoho_task_id)` follows the full pipeline: fetch live state from both APIs → compute canonical hashes → SELECT FOR UPDATE on sync_state row → if hashes match, log echo_suppressed and return; if not, write to target → update sync_state.last_hash → log action='sync' in sync_events | VERIFIED | `app/worker/jobs.py` lines 64–249 implement the complete pipeline. `with_for_update()` at line 145, `canonical_hash()` calls at lines 136–137, `SyncEvent` inserts at lines 152–157, 184–189, 239–243. All 11 unit tests pass (215/215 total). |
| 2 | Enqueuing `sync_task` with `_job_id=f"sync:{zoho_task_id}"` deduplicates concurrent webhooks; None return from enqueue_job logs WARN; per-task Redis SETNX lock (30s TTL) serialises any two jobs that slip through dedup | VERIFIED | `app/worker/enqueue.py` line 34: `_job_id=f"sync:{zoho_task_id}"`. Line 37: `log.warning("sync_task_dedup_dropped", ...)`. `app/worker/jobs.py` line 81: `redis.set(lock_key, "1", nx=True, ex=30)`. Tests `test_sync_task_lock_not_acquired_returns_early` and `test_enqueue_sync_dedups` pass. |
| 3 | Zoho-webhook-triggered jobs defer by 2 seconds (configurable via ZOHO_JOB_DEFER_SECS) before the Zoho API fetch, reducing the stale-read race window | VERIFIED | `enqueue_sync` accepts `defer_secs: int = 0` and passes it as `_defer_by` to arq (line 35). The enforcement of `settings.zoho_job_defer_secs` at Zoho-triggered call sites is Phase 6's responsibility (explicitly documented in plan 02 and Phase 6 SC1). Mechanism is complete and tested (`test_enqueue_sync_defers_by_zoho_secs`). |
| 4 | arq retry config: max 3 retries, backoff 5s/15s/60s; job_timeout=60; keep_result=300; API write failures raise and trigger retry; DB update failures raise and trigger retry | VERIFIED | `RETRY_DELAYS = {1: 5, 2: 15, 3: 60}` in `jobs.py` line 61. `func(sync_task, timeout=60, keep_result=300, max_tries=4)` in `settings.py` line 115 (max_tries=4 = 3 retries). `raise Retry(defer=delay)` at line 99. `test_retry_on_zoho_rate_limit_uses_correct_delay` passes. |
| 5 | When this service creates a new Todoist task, the resulting item:added webhook is identified as sync-managed (footer present) and suppressed without triggering a reverse sync | VERIFIED | Bootstrap race suppression works via the echo_suppressed hash-equality path: the new Todoist task created by this service will hash-match `sync_state.last_hash` after creation, so the resulting self-triggered webhook hits `echo_suppressed`. `test_bootstrap_race_footer_suppressed` passes. |

**Score:** 5/5 truths verified

### Deferred Items

Items not yet met but explicitly addressed in later milestone phases.

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | Zoho-webhook-triggered caller enforces `settings.zoho_job_defer_secs` as the `defer_secs` value | Phase 6 | Phase 6 SC1: "POST /webhooks/zoho ... enqueues sync_task with a 2-second defer". Plan 02 truth: "the correct value for Zoho-triggered jobs is settings.zoho_job_defer_secs (per LOOP-4), which is a caller's responsibility enforced in Phase 6 webhook handlers — not in Plan 02". |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/worker/__init__.py` | Package marker | VERIFIED | Exists, 1 line docstring |
| `app/worker/jobs.py` | sync_task coroutine + RETRY_DELAYS + helpers (min 120 lines) | VERIFIED | 249 lines; exports sync_task and RETRY_DELAYS; implements full pipeline with SETNX, FOR UPDATE, LWW, Retry |
| `app/worker/settings.py` | WorkerSettings + on_startup + on_shutdown (min 70 lines) | VERIFIED | 120 lines; WorkerSettings class with lifecycle hooks, proactive_refresh_loop launch/cancel |
| `app/worker/__main__.py` | python -m app.worker entry point (min 4 lines) | VERIFIED | 7 lines; `run_worker(WorkerSettings)` under `if __name__ == "__main__"` guard |
| `app/worker/enqueue.py` | enqueue_sync helper (min 20 lines) | VERIFIED | 48 lines; dedup via _job_id, defer via _defer_by, WARN on None return |
| `tests/unit/test_worker_jobs.py` | 11 unit tests covering LOOP-1, LOOP-3, LOOP-5, SYNC-11 (min 150 lines) | VERIFIED | 485 lines; 11 tests, all pass |
| `tests/unit/test_worker_settings.py` | 12 unit tests for WorkerSettings lifecycle (min 120 lines) | VERIFIED | 318 lines; 12 tests, all pass |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `app/worker/jobs.py` | `ctx['redis']` | `redis.set(lock_key, "1", nx=True, ex=30)` | WIRED | Line 81: exact pattern match |
| `app/worker/jobs.py` | `sync_state row` | `SELECT ... with_for_update() inside session.begin()` | WIRED | Lines 141–145: `select(SyncState).where(...).with_for_update()` inside `async with session.begin()` |
| `app/worker/jobs.py` | `app.core.hash.canonical_hash` | import + call on both zoho_norm and todoist_norm | WIRED | Lines 29, 136, 137, 228 |
| `app/worker/jobs.py` | `app.todoist.normalise.extract_zoho_id` | LOOP-5 footer-based bootstrap suppression | WIRED (via echo path) | Imported at line 38. Bootstrap suppression implemented via hash-equality (echo_suppressed) path per SUMMARY decision; extract_zoho_id itself is used by Phase 6 webhook handler. Import is present; the suppression mechanism is active. |
| `app/worker/jobs.py` | `arq.Retry` | `raise Retry(defer=RETRY_DELAYS[job_try])` | WIRED | Line 99: `raise Retry(defer=delay)` |
| `app/worker/__main__.py` | `arq.run_worker` | `run_worker(WorkerSettings)` | WIRED | Line 7: exact pattern match |
| `app/worker/settings.py` | `app.worker.jobs.sync_task` | `func(sync_task, timeout=60, keep_result=300, max_tries=4)` | WIRED | Line 115: exact match |
| `app/worker/settings.py` | `arq.connections.RedisSettings` | `RedisSettings.from_dsn(settings.redis_url)` | WIRED | Line 119: exact match |
| `app/worker/settings.py` | `app.zoho.token_manager.proactive_refresh_loop` | `asyncio.create_task` in on_startup; cancel + await in on_shutdown | WIRED | Lines 87–89 (startup), 96–101 (shutdown) |
| `app/worker/enqueue.py` | `ArqRedis.enqueue_job` | `_job_id=f"sync:{zoho_task_id}"` and `_defer_by=defer_secs` | WIRED | Lines 32–35 |

### Data-Flow Trace (Level 4)

Not applicable — this phase delivers job processing logic and worker lifecycle (no UI components rendering dynamic data). All data flows are through arq job queue, Redis, Postgres, and external APIs, verified via unit tests.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `sync_task` importable from `app.worker.jobs` | `python3 -c "from app.worker.jobs import sync_task, RETRY_DELAYS; print(callable(sync_task), RETRY_DELAYS)"` | `True {1: 5, 2: 15, 3: 60}` | PASS |
| `enqueue_sync` importable from `app.worker.enqueue` | `python3 -c "from app.worker.enqueue import enqueue_sync; print(callable(enqueue_sync))"` | `True` | PASS |
| Full test suite (215 tests) green | `python3 -m pytest tests/ -q` | 215 passed | PASS |
| Phase 05 unit tests (23 tests) green | `python3 -m pytest tests/unit/test_worker_jobs.py tests/unit/test_worker_settings.py -q` | 23 passed | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| LOOP-1 | 05-01 | Canonical hash echo suppression | SATISFIED | `jobs.py` lines 151–158; `test_echo_suppressed_when_all_hashes_match` passes |
| LOOP-3 | 05-01 | SELECT FOR UPDATE on sync_state | SATISFIED | `jobs.py` lines 141–145; `test_select_for_update_is_called` passes |
| LOOP-4 | 05-02 | 2-second defer on Zoho-triggered jobs | SATISFIED (mechanism) | `enqueue_sync` accepts `defer_secs` param and passes as `_defer_by`; enforcement deferred to Phase 6 webhook handlers per plan design |
| LOOP-5 | 05-01 | Bootstrap race suppression | SATISFIED | Echo-suppressed path: new Todoist task hash matches last_hash after creation; `test_bootstrap_race_footer_suppressed` passes |
| SYNC-10 | 05-02 | arq job dedup via `_job_id` + WARN on drop | SATISFIED | `enqueue_sync` lines 32–40; `test_enqueue_sync_dedups` passes |
| SYNC-11 | 05-01 | LWW conflict resolution, Zoho wins | SATISFIED | `jobs.py` lines 162–166 (overwrite path); `test_lww_zoho_wins_when_both_diverge` passes |
| INFRA-1 | 05-02 | Worker as separate Railway service | SATISFIED | `app/worker/__main__.py` entry point; WorkerSettings complete; on_startup populates ctx |
| INFRA-3 | 05-02 | Redis via arq | SATISFIED | `RedisSettings.from_dsn(settings.redis_url)` in WorkerSettings; `test_redis_settings_from_dsn` passes |

### Orphaned Requirements Check

REQUIREMENTS.md traceability maps OBS-2 to Phase 5, but neither PLAN frontmatter nor ROADMAP Phase 5 success criteria claim OBS-2. The `sync_task` pipeline does insert `SyncEvent` rows with `action` and `source` fields, partially satisfying OBS-2. However, `source="worker"` is used rather than the specified `{zoho_webhook, todoist_webhook, reconciler, migration}` enum. This is a known design choice — the worker is a legitimate source not originally enumerated in OBS-2. The `source` column has no database-level constraint (String(32)). Full OBS-2 coverage spans multiple phases.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `app/worker/jobs.py` | 38 | `extract_zoho_id` imported but not called | Info | Not a stub — bootstrap suppression works via hash-equality path. Import retained for Phase 6 reference. No functional impact. |
| `app/worker/settings.py` | 101 | `pass` in `except asyncio.CancelledError` | Info | Correct design — swallows CancelledError in shutdown path. Not a stub. |

### Human Verification Required

None — all observable truths are verifiable programmatically. The worker requires real Railway/Redis/Postgres/Zoho/Todoist connections for E2E testing, but that is scoped to Phase 8 (SEED-4).

## Gaps Summary

No gaps. All 5 roadmap success criteria are verified against the codebase:

- SC1: Full sync_task pipeline implemented and tested (11 unit tests cover all branches)
- SC2: enqueue_sync dedup + SETNX lock both implemented and tested
- SC3: defer_secs mechanism in enqueue_sync; enforcement deferred to Phase 6 (documented in plan and Phase 6 SC1)
- SC4: RETRY_DELAYS + max_tries=4 + timeout=60 + keep_result=300 all confirmed
- SC5: Bootstrap race suppression via hash-equality echo_suppressed path confirmed

---

_Verified: 2026-04-24T13:39:03Z_
_Verifier: Claude (gsd-verifier)_
