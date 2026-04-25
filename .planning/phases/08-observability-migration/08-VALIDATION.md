---
phase: 8
slug: observability-migration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-25
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `pytest.ini` or `pyproject.toml [tool.pytest]` |
| **Quick run command** | `pytest tests/ -x -q` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 8-01-01 | 01 | 1 | OBS-1 | — | Health returns 200/503 only, no secrets in response | unit | `pytest tests/test_health.py -x -q` | ❌ W0 | ⬜ pending |
| 8-01-02 | 01 | 1 | OBS-2 | — | Health uses only DB/Redis cached values | unit | `pytest tests/test_health.py -x -q` | ❌ W0 | ⬜ pending |
| 8-02-01 | 02 | 2 | OBS-3 | — | Cron creates summary task and purges old events | unit | `pytest tests/test_cron.py -x -q` | ❌ W0 | ⬜ pending |
| 8-03-01 | 03 | 3 | SEED-1, SEED-2 | — | Migration links task pairs idempotently | integration | `pytest tests/test_migration.py -x -q` | ❌ W0 | ⬜ pending |
| 8-03-02 | 03 | 3 | SEED-3, SEED-4 | — | Migration replaces Make.com preamble, no duplicates | integration | `pytest tests/test_migration.py -x -q` | ❌ W0 | ⬜ pending |
| 8-04-01 | 04 | 4 | INFRA-5 | — | E2E test completes full sync cycle without infinite loop | e2e | `pytest tests/test_e2e.py -x -q -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_health.py` — stubs for OBS-1, OBS-2
- [ ] `tests/test_cron.py` — stubs for OBS-3
- [ ] `tests/test_migration.py` — stubs for SEED-1, SEED-2, SEED-3, SEED-4
- [ ] `tests/test_e2e.py` — stubs for INFRA-5

*Existing `tests/` infrastructure (conftest, fixtures) from prior phases covers shared test utilities.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `/health` responds within 100ms | OBS-1 | Requires live Railway deployment | Curl health endpoint, check response time header or measure manually |
| 10 real sync events post-migration with zero infinite loops | INFRA-5 | Requires live Zoho + Todoist accounts and real traffic | Monitor `sync_events` table after migration; count events; verify no duplicate entries |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
