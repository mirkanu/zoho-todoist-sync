---
phase: 1
slug: foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-23
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pyproject.toml` (Wave 0 creates it) |
| **Quick run command** | `pytest tests/ -x -q` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 1-01-01 | 01 | 0 | INFRA-1 | — | N/A | infra | `test -f pyproject.toml` | ❌ W0 | ⬜ pending |
| 1-01-02 | 01 | 0 | INFRA-2 | — | N/A | infra | `test -f alembic.ini` | ❌ W0 | ⬜ pending |
| 1-01-03 | 01 | 1 | INFRA-3 | — | N/A | unit | `pytest tests/test_config.py -x -q` | ❌ W0 | ⬜ pending |
| 1-01-04 | 01 | 1 | INFRA-4 | — | fail-fast on missing env vars | unit | `pytest tests/test_config.py::test_missing_var_raises -x -q` | ❌ W0 | ⬜ pending |
| 1-02-01 | 02 | 1 | SYNC-2 | — | N/A | unit | `pytest tests/test_hash.py -x -q` | ❌ W0 | ⬜ pending |
| 1-02-02 | 02 | 1 | LOOP-1 | — | N/A | unit | `pytest tests/test_hash.py::test_date_normalisation -x -q` | ❌ W0 | ⬜ pending |
| 1-02-03 | 02 | 1 | LOOP-2 | — | N/A | unit | `pytest tests/test_hash.py::test_echo_detection -x -q` | ❌ W0 | ⬜ pending |
| 1-02-04 | 02 | 1 | SYNC-5 | — | N/A | unit | `pytest tests/test_hash.py::test_priority_roundtrip -x -q` | ❌ W0 | ⬜ pending |
| 1-02-05 | 02 | 1 | SYNC-7 | — | N/A | unit | `pytest tests/test_hash.py::test_footer_stripping -x -q` | ❌ W0 | ⬜ pending |
| 1-02-06 | 02 | 1 | SYNC-9 | — | N/A | unit | `pytest tests/test_hash.py::test_unicode_nfc -x -q` | ❌ W0 | ⬜ pending |
| 1-03-01 | 03 | 1 | INFRA-5 | — | N/A | unit | `pytest tests/test_models.py -x -q` | ❌ W0 | ⬜ pending |
| 1-03-02 | 03 | 1 | INFRA-6 | — | N/A | infra | `alembic upgrade head && alembic check` | ❌ W0 | ⬜ pending |
| 1-03-03 | 03 | 1 | INFRA-7 | — | N/A | infra | `alembic downgrade base && alembic upgrade head` | ❌ W0 | ⬜ pending |
| 1-04-01 | 04 | 1 | OBS-5 | — | N/A | unit | `pytest tests/test_logging.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/__init__.py` — package marker
- [ ] `tests/conftest.py` — shared fixtures (env var mocks, DB URL override)
- [ ] `tests/test_config.py` — stubs for INFRA-3, INFRA-4
- [ ] `tests/test_hash.py` — stubs for SYNC-2, LOOP-1, LOOP-2, SYNC-5, SYNC-7, SYNC-9
- [ ] `tests/test_models.py` — stubs for INFRA-5
- [ ] `tests/test_logging.py` — stubs for OBS-5
- [ ] `pyproject.toml` — pytest config + dependencies declaration

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Migration applies cleanly from scratch against a real Postgres | INFRA-2 | Requires live DB | `docker run -e POSTGRES_PASSWORD=test postgres:16; alembic upgrade head` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
