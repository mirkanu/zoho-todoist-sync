---
phase: 3
slug: todoist-read
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-24
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (already installed) |
| **Config file** | `pytest.ini` or `pyproject.toml [tool.pytest]` |
| **Quick run command** | `pytest tests/unit/ -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/unit/ -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 3-01-01 | 01 | 1 | SYNC-5 | — | `extract_zoho_id()` returns None for missing footer, zoho_id for valid, None for malformed | unit | `pytest tests/unit/test_todoist_normalise.py -q` | ❌ W0 | ⬜ pending |
| 3-01-02 | 01 | 1 | SYNC-8 | — | Items without footer are logged and discarded; no zoho_id processed | unit | `pytest tests/unit/test_todoist_normalise.py -q` | ❌ W0 | ⬜ pending |
| 3-02-01 | 02 | 1 | SYNC-9 | — | `todoist_task_to_normalised()` never includes `labels` field | unit | `pytest tests/unit/test_todoist_normalise.py -q` | ❌ W0 | ⬜ pending |
| 3-02-02 | 02 | 1 | SYNC-5 | — | `fetch_todoist_task()` returns typed Task or raises typed exception; 401→TodoistAuthError | unit | `pytest tests/unit/test_todoist_client.py -q` | ❌ W0 | ⬜ pending |
| 3-03-01 | 03 | 2 | SEED-7 | — | startup_sync uses `sync_token="*"` first time; persists returned token to kv_store | unit | `pytest tests/unit/test_todoist_sync_manager.py -q` | ❌ W0 | ⬜ pending |
| 3-03-02 | 03 | 2 | SEED-7 | — | On restart, stored sync_token loaded from kv_store; incremental sync resumes | unit | `pytest tests/unit/test_todoist_sync_manager.py -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_todoist_normalise.py` — stubs for SYNC-5, SYNC-8, SYNC-9
- [ ] `tests/unit/test_todoist_client.py` — stubs for SYNC-5 (auth error mapping)
- [ ] `tests/unit/test_todoist_sync_manager.py` — stubs for SEED-7

*Existing test infrastructure (`tests/unit/`, pytest) already present from Phase 2.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Auth failure stops sync and triggers alert | SYNC-5 | Requires live Todoist API call with invalid token to verify alert email sent | Set `TODOIST_API_TOKEN=invalid`, trigger sync, confirm Resend alert received |

---

## Validation Architecture

The following test files must exist before execution begins (Wave 0):

- `tests/unit/test_todoist_normalise.py` — unit tests for `extract_zoho_id()` covering: missing footer, footer mid-text, footer after user edits; and `todoist_task_to_normalised()` excluding labels
- `tests/unit/test_todoist_client.py` — unit tests for `TodoistClient.fetch_todoist_task()` with mocked httpx responses covering 200, 401, 404, 429, 500
- `tests/unit/test_todoist_sync_manager.py` — unit tests for `startup_sync()` covering: no stored token → uses `"*"`, stored token → uses stored, new token persisted after sync

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
