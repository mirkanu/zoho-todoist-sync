---
phase: 5
slug: arq-worker
status: draft
nyquist_compliant: false
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
| **Config file** | `pytest.ini` or `pyproject.toml [tool.pytest]` |
| **Quick run command** | `pytest tests/test_worker.py -x -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_worker.py -x -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 5-01-01 | 01 | 1 | SYNC-10 | — | N/A | unit | `pytest tests/test_worker.py::test_sync_task_pipeline -x -q` | ❌ W0 | ⬜ pending |
| 5-01-02 | 01 | 1 | LOOP-1 | — | Echo suppression prevents reverse sync loop | unit | `pytest tests/test_worker.py::test_echo_suppression -x -q` | ❌ W0 | ⬜ pending |
| 5-01-03 | 01 | 1 | LOOP-3 | — | SETNX lock prevents concurrent job collision | unit | `pytest tests/test_worker.py::test_per_task_lock -x -q` | ❌ W0 | ⬜ pending |
| 5-01-04 | 01 | 1 | LOOP-4 | — | Dedup via _job_id prevents duplicate enqueue | unit | `pytest tests/test_worker.py::test_job_deduplication -x -q` | ❌ W0 | ⬜ pending |
| 5-01-05 | 01 | 1 | LOOP-5 | — | 2s defer reduces stale-read race window | unit | `pytest tests/test_worker.py::test_zoho_defer -x -q` | ❌ W0 | ⬜ pending |
| 5-01-06 | 01 | 1 | SYNC-11 | — | Retry config: max 3 retries, backoff 5/15/60s | unit | `pytest tests/test_worker.py::test_retry_config -x -q` | ❌ W0 | ⬜ pending |
| 5-01-07 | 01 | 1 | INFRA-1 | — | Worker startup initialises DB pool and token refresh | unit | `pytest tests/test_worker.py::test_on_startup -x -q` | ❌ W0 | ⬜ pending |
| 5-01-08 | 01 | 1 | INFRA-3 | — | Worker settings loaded from env vars | unit | `pytest tests/test_worker.py::test_worker_settings -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_worker.py` — stubs for all SYNC-10, SYNC-11, LOOP-1, LOOP-3, LOOP-4, LOOP-5, INFRA-1, INFRA-3 tests
- [ ] `tests/conftest.py` — shared fixtures (mock ArqRedis, mock DB session, mock ZohoClient, mock TodoistClient)

*Existing pytest infrastructure in `tests/` already present — Wave 0 extends it.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Worker stays alive >50 min without token expiry | INFRA-1 | Requires real Zoho OAuth flow + timer | Start worker, let run 55 min, verify subsequent sync_task calls succeed |
| Todoist `item:added` webhook suppressed for sync-managed tasks | LOOP-1 | Requires live Todoist webhook delivery | Create task via sync_task, verify no reverse sync job enqueued |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
