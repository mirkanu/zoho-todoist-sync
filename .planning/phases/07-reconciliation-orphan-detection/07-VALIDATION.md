---
phase: 7
slug: reconciliation-orphan-detection
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-24
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio 0.28.0 |
| **Config file** | `pyproject.toml` (`asyncio_mode = "auto"`) |
| **Quick run command** | `python -m pytest tests/unit/test_reconciler.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/unit/test_reconciler.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 7-01-01 | 01 | 0 | SEED-5, SEED-6 | — | N/A | unit | `pytest tests/unit/test_reconciler.py -x -q` | ❌ W0 | ⬜ pending |
| 7-01-02 | 01 | 1 | SEED-5 | — | N/A | unit | `pytest tests/unit/test_reconciler.py::test_reconcile_zoho_mismatch -x` | ❌ W0 | ⬜ pending |
| 7-01-03 | 01 | 1 | SEED-5 | — | N/A | unit | `pytest tests/unit/test_reconciler.py::test_reconcile_todoist_delta -x` | ❌ W0 | ⬜ pending |
| 7-01-04 | 01 | 1 | SEED-5 | — | N/A | unit | `pytest tests/unit/test_reconciler.py::test_sync_token_saved tests/unit/test_reconciler.py::test_reconciler_last_run_updated -x` | ❌ W0 | ⬜ pending |
| 7-02-01 | 02 | 0 | SEED-6 | T-7-01 | Two-cycle confirmation before deletion; rate-limit errors skip row | unit | `pytest tests/unit/test_reconciler.py -x -q` | ❌ W0 | ⬜ pending |
| 7-02-02 | 02 | 1 | SEED-6, EDGE-5 | T-7-01 | First 404 only increments count | unit | `pytest tests/unit/test_reconciler.py::test_orphan_first_cycle -x` | ❌ W0 | ⬜ pending |
| 7-02-03 | 02 | 1 | SEED-6, EDGE-1 | T-7-02 | Reassignment detected via Owner.id, not 404 | unit | `pytest tests/unit/test_reconciler.py::test_orphan_reassignment_detected -x` | ❌ W0 | ⬜ pending |
| 7-02-04 | 02 | 1 | SEED-6, EDGE-2 | T-7-02 | Todoist 404 triggers Zoho deletion + notification | unit | `pytest tests/unit/test_reconciler.py::test_orphan_todoist_missing -x` | ❌ W0 | ⬜ pending |
| 7-02-05 | 02 | 1 | SEED-6 | — | N/A | unit | `pytest tests/unit/test_reconciler.py::test_orphan_count_reset -x` | ❌ W0 | ⬜ pending |
| 7-02-06 | 02 | 1 | EDGE-8 | — | N/A | unit | `pytest tests/unit/test_reconciler.py::test_refooter_missing_footer -x` | ❌ W0 | ⬜ pending |
| 7-02-07 | 02 | 2 | SYNC-10, WorkerSettings | — | N/A | unit | `pytest tests/unit/test_worker_settings.py::test_cron_jobs_registered tests/unit/test_reconciler.py::test_reconcile_dedup -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_reconciler.py` — stubs for SEED-5, SEED-6, EDGE-1, EDGE-2, EDGE-5, EDGE-8, SYNC-10 (≥12 test cases)
- [ ] `tests/unit/test_worker_settings.py` — add `test_cron_jobs_registered` stub

*Existing infrastructure (pytest + pytest-asyncio) covers all phase requirements — no new framework install needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Reconciler catches missed webhook (simulated downtime) | SEED-5 SC-2 | Requires live Railway environment + deliberate webhook receiver downtime | Edit a Zoho task, stop the web service for 5 min, verify edit appears in Todoist within 20 min via reconciler alone |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
