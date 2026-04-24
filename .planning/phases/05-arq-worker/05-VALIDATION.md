---
phase: 5
slug: arq-worker
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-04-24
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (asyncio) |
| **Config file** | `pyproject.toml [tool.pytest.ini_options]` |
| **Quick run command** | `python -m pytest tests/unit/test_worker_jobs.py tests/unit/test_worker_settings.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/unit/test_worker_jobs.py tests/unit/test_worker_settings.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 5-01-01 | 01 | 1 | LOOP-1 | — | Echo suppression prevents reverse sync loop | unit | `python -m pytest tests/unit/test_worker_jobs.py::test_echo_suppressed_when_all_hashes_match -x -q` | ❌ W0 | ⬜ pending |
| 5-01-02 | 01 | 1 | LOOP-3 | T-05-03 | SELECT FOR UPDATE serialises row-level writes | unit | `python -m pytest tests/unit/test_worker_jobs.py::test_select_for_update_is_called -x -q` | ❌ W0 | ⬜ pending |
| 5-01-03 | 01 | 1 | LOOP-5 | — | Footer-tagged Todoist tasks don't trigger reverse sync | unit | `python -m pytest tests/unit/test_worker_jobs.py::test_bootstrap_race_footer_suppressed -x -q` | ❌ W0 | ⬜ pending |
| 5-01-04 | 01 | 1 | SYNC-11 | T-05-06 | LWW: Zoho wins on simultaneous divergence, overwrite event logged | unit | `python -m pytest tests/unit/test_worker_jobs.py::test_lww_zoho_wins_when_both_diverge -x -q` | ❌ W0 | ⬜ pending |
| 5-01-05 | 01 | 1 | SYNC-11 | T-05-02 | SETNX per-task lock serialises concurrent jobs | unit | `python -m pytest tests/unit/test_worker_jobs.py::test_sync_task_lock_not_acquired_returns_early -x -q` | ❌ W0 | ⬜ pending |
| 5-01-06 | 01 | 1 | SYNC-11 | T-05-04 | Retry backoff: 5s/15s/60s based on ctx['job_try'] | unit | `python -m pytest tests/unit/test_worker_jobs.py::test_retry_on_zoho_rate_limit_uses_correct_delay -x -q` | ❌ W0 | ⬜ pending |
| 5-02-01 | 02 | 2 | INFRA-3 | — | RedisSettings.from_dsn consumes REDIS_URL | unit | `python -m pytest tests/unit/test_worker_settings.py::test_redis_settings_from_dsn -x -q` | ❌ W0 | ⬜ pending |
| 5-02-02 | 02 | 2 | INFRA-1 | — | Worker startup initialises DB pool, token, clients, refresh loop | unit | `python -m pytest tests/unit/test_worker_settings.py::test_on_startup_populates_ctx -x -q` | ❌ W0 | ⬜ pending |
| 5-02-03 | 02 | 2 | INFRA-1 | T-05-15 | Proactive token refresh loop launched in on_startup | unit | `python -m pytest tests/unit/test_worker_settings.py::test_on_startup_launches_proactive_refresh_loop -x -q` | ❌ W0 | ⬜ pending |
| 5-02-04 | 02 | 2 | INFRA-1 | — | on_shutdown cancels refresh task and closes clients | unit | `python -m pytest tests/unit/test_worker_settings.py::test_on_shutdown_cancels_refresh_task_and_closes_clients -x -q` | ❌ W0 | ⬜ pending |
| 5-02-05 | 02 | 2 | INFRA-1 | — | Worker entry point calls run_worker(WorkerSettings) | unit | `python -m pytest tests/unit/test_worker_settings.py::test_main_module_calls_run_worker -x -q` | ❌ W0 | ⬜ pending |
| 5-02-06 | 02 | 2 | SYNC-10 | T-05-13 | enqueue_sync dedup via _job_id; WARN log on duplicate | unit | `python -m pytest tests/unit/test_worker_settings.py::test_enqueue_sync_dedups -x -q` | ❌ W0 | ⬜ pending |
| 5-02-07 | 02 | 2 | LOOP-4 | — | enqueue_sync forwards defer_secs as _defer_by | unit | `python -m pytest tests/unit/test_worker_settings.py::test_enqueue_sync_defers_by_zoho_secs -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_worker_jobs.py` — stubs for LOOP-1, LOOP-3, LOOP-5, SYNC-11 (covered by Plan 01 Task 1)
- [ ] `tests/unit/test_worker_settings.py` — stubs for INFRA-1, INFRA-3, SYNC-10, LOOP-4 (covered by Plan 02 Task 1)
- [ ] `app/worker/__init__.py` — package marker (Plan 01 Task 1)

*Existing pytest infrastructure in `tests/` already present; fixtures (`complete_env`, session-factory helpers) are reused.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Worker stays alive >50 min without token expiry | INFRA-1 | Requires real Zoho OAuth flow + timer; proactive_refresh_loop timing can only be observed in a long-lived process | Start worker, let it run 55 min, verify subsequent sync_task calls succeed and logs show `zoho_token_refresh` events from the background loop |
| Todoist `item:added` webhook suppressed for sync-managed tasks (end-to-end) | LOOP-5 | Requires live Todoist webhook delivery | Create task via sync_task, verify no reverse sync job enqueued for the footer-tagged Todoist item |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (test file paths aligned with Plan 01/02 `<verify>` blocks)
- [x] No watch-mode flags
- [x] Feedback latency < 30s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
