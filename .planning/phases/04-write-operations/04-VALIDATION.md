---
phase: 4
slug: write-operations
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-24
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pytest.ini or pyproject.toml |
| **Quick run command** | `pytest tests/test_write_ops.py -x -q` |
| **Full suite command** | `pytest tests/ -x -q` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_write_ops.py -x -q`
- **After every plan wave:** Run `pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 4-01-01 | 01 | 1 | SYNC-1 | — | N/A | unit | `pytest tests/test_write_ops.py::test_create_todoist_task -x -q` | ❌ W0 | ⬜ pending |
| 4-01-02 | 01 | 1 | SYNC-2 | — | N/A | unit | `pytest tests/test_write_ops.py::test_update_todoist_task -x -q` | ❌ W0 | ⬜ pending |
| 4-01-03 | 01 | 1 | SYNC-3 | — | N/A | unit | `pytest tests/test_write_ops.py::test_update_zoho_task -x -q` | ❌ W0 | ⬜ pending |
| 4-01-04 | 01 | 1 | SYNC-6 | — | N/A | unit | `pytest tests/test_write_ops.py::test_complete_tasks -x -q` | ❌ W0 | ⬜ pending |
| 4-01-05 | 01 | 1 | SYNC-8 | — | N/A | unit | `pytest tests/test_write_ops.py::test_priority_mapping -x -q` | ❌ W0 | ⬜ pending |
| 4-01-06 | 01 | 1 | EDGE-1 | — | N/A | unit | `pytest tests/test_write_ops.py::test_due_date_none_clears -x -q` | ❌ W0 | ⬜ pending |
| 4-01-07 | 01 | 1 | EDGE-2 | — | N/A | unit | `pytest tests/test_write_ops.py::test_terminal_status_env -x -q` | ❌ W0 | ⬜ pending |
| 4-01-08 | 01 | 1 | EDGE-3 | — | N/A | unit | `pytest tests/test_write_ops.py::test_delete_sends_email -x -q` | ❌ W0 | ⬜ pending |
| 4-01-09 | 01 | 1 | EDGE-4 | — | N/A | unit | `pytest tests/test_write_ops.py::test_resend_failure_no_rollback -x -q` | ❌ W0 | ⬜ pending |
| 4-01-10 | 01 | 1 | EDGE-6 | — | N/A | unit | `pytest tests/test_write_ops.py::test_idempotency -x -q` | ❌ W0 | ⬜ pending |
| 4-01-11 | 01 | 1 | EDGE-7 | — | N/A | unit | `pytest tests/test_write_ops.py::test_todoist_labels_untouched -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_write_ops.py` — stubs for SYNC-1, SYNC-2, SYNC-3, SYNC-6, SYNC-8, EDGE-1, EDGE-2, EDGE-3, EDGE-4, EDGE-6, EDGE-7
- [ ] `tests/conftest.py` — shared fixtures (mock Todoist SDK, mock httpx for Zoho, mock resend)
- [ ] `pytest` — already installed (from Phase 3 infra)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Resend sender domain verified | EDGE-3 | Requires live Resend dashboard check | Check Resend dashboard for verified domain; confirm `from` address matches |
| Zoho null Due_Date clears field | EDGE-1 | Live API behaviour — cannot mock reliably | POST `{"Due_Date": null}` to Zoho sandbox task; confirm field is cleared |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
