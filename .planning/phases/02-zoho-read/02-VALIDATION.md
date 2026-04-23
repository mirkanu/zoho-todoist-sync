---
phase: 2
slug: zoho-read
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-23
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pyproject.toml` (already configured in Phase 1) |
| **Quick run command** | `pytest tests/unit/test_zoho_client.py -x -q` |
| **Full suite command** | `pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/unit/test_zoho_client.py -x -q`
- **After every plan wave:** Run `pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 2-01-01 | 01 | 1 | INFRA-6 | — | Token refresh stores new token in kv_store; failure raises not silently retried | unit | `pytest tests/unit/test_token_manager.py -x -q` | ❌ W0 | ⬜ pending |
| 2-01-02 | 01 | 1 | INFRA-6 | — | proactive_refresh_loop runs in FastAPI lifespan and arq on_startup | unit | `pytest tests/unit/test_token_manager.py -x -q` | ❌ W0 | ⬜ pending |
| 2-02-01 | 02 | 1 | INFRA-7 | — | Startup resolves Todoist Task ID api_name from field metadata; caches result | unit | `pytest tests/unit/test_zoho_client.py::test_get_fields_metadata -x -q` | ❌ W0 | ⬜ pending |
| 2-02-02 | 02 | 1 | INFRA-7 | — | Startup resolves Status picklist values and compares against ZOHO_TERMINAL_STATUSES | unit | `pytest tests/unit/test_zoho_client.py::test_status_picklist -x -q` | ❌ W0 | ⬜ pending |
| 2-03-01 | 03 | 2 | SYNC-4 | — | fetch_zoho_task returns normalised dict; raises ZohoNotFoundError on 404 | unit | `pytest tests/unit/test_zoho_client.py::test_fetch_zoho_task -x -q` | ❌ W0 | ⬜ pending |
| 2-03-02 | 03 | 2 | SYNC-4 | — | fetch_zoho_task raises ZohoAuthError on 401, ZohoRateLimitError on 429 | unit | `pytest tests/unit/test_zoho_client.py::test_fetch_error_mapping -x -q` | ❌ W0 | ⬜ pending |
| 2-04-01 | 04 | 2 | LOOP-4 | — | fetch_zoho_tasks_modified_since paginates via more_records; always includes Modified_Time + Owner filter | unit | `pytest tests/unit/test_zoho_client.py::test_fetch_modified_since -x -q` | ❌ W0 | ⬜ pending |
| 2-04-02 | 04 | 2 | LOOP-4 | — | 204 response from search treated as empty list (not error) | unit | `pytest tests/unit/test_zoho_client.py::test_fetch_204_empty -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_zoho_client.py` — stubs for INFRA-7, SYNC-4, LOOP-4
- [ ] `tests/unit/test_token_manager.py` — stubs for INFRA-6
- [ ] `tests/conftest.py` — shared fixtures (mock httpx, db session factory)

*Existing pytest infrastructure from Phase 1 covers the framework install.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Actual `api_name` value logged at startup | INFRA-7 | Cannot determine without live Zoho API call | Check logs after first deployment for `zoho_field_resolved` log entry |
| Token refresh runs every 50 min in production | INFRA-6 | Long timing interval not practical in unit tests | Monitor `zoho_token_refreshed` log events in production |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
