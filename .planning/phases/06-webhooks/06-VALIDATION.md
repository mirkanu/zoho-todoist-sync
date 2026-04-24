---
phase: 6
slug: webhooks
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-24
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `python3 -m pytest tests/unit/test_webhooks.py -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/unit/test_webhooks.py -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -x -q`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | SYNC-4 | — | Zoho handler extracts `ids[0]`, enqueues with 2s defer | unit | `python3 -m pytest tests/unit/test_webhooks.py::test_zoho_webhook_enqueues -x -q` | ❌ W0 | ⬜ pending |
| 06-01-02 | 01 | 1 | SYNC-4 | — | Missing `module` or empty `ids` → HTTP 400 | unit | `python3 -m pytest tests/unit/test_webhooks.py::test_zoho_missing_module_returns_400 -x -q` | ❌ W0 | ⬜ pending |
| 06-01-03 | 01 | 1 | INFRA-4 | — | Router registered at `/webhooks/zoho` and `/webhooks/todoist` | unit | `python3 -m pytest tests/unit/test_webhooks.py::test_router_paths -x -q` | ❌ W0 | ⬜ pending |
| 06-02-01 | 02 | 1 | INFRA-1 | T-6-HMAC | HMAC mismatch → HTTP 401; raw body used (not parsed JSON) | unit | `python3 -m pytest tests/unit/test_webhooks.py::test_todoist_invalid_hmac_returns_401 -x -q` | ❌ W0 | ⬜ pending |
| 06-02-02 | 02 | 1 | SYNC-8 | — | `item:added` without footer → discarded (log + return 200) | unit | `python3 -m pytest tests/unit/test_webhooks.py::test_todoist_item_added_no_footer_discarded -x -q` | ❌ W0 | ⬜ pending |
| 06-02-03 | 02 | 1 | LOOP-5 | — | `item:added` with footer → enqueues sync_task (not discarded) | unit | `python3 -m pytest tests/unit/test_webhooks.py::test_todoist_item_added_with_footer_enqueues -x -q` | ❌ W0 | ⬜ pending |
| 06-02-04 | 02 | 1 | EDGE-7 | — | `item:completed` → lookup sync_state → enqueue | unit | `python3 -m pytest tests/unit/test_webhooks.py::test_todoist_item_completed_enqueues -x -q` | ❌ W0 | ⬜ pending |
| 06-02-05 | 02 | 1 | EDGE-8 | — | Missing footer on sync_state task → log WARN, return 200 | unit | `python3 -m pytest tests/unit/test_webhooks.py::test_todoist_missing_footer_on_synced_task -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_webhooks.py` — all webhook handler tests (stubs for all 8 behaviors above)
- [ ] `app/webhooks/__init__.py` — package marker
- [ ] `app/webhooks/router.py` — webhook router module

*Existing infrastructure covers test framework — `conftest.py` and pytest-asyncio already configured.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| ArqRedis pool closes cleanly at shutdown | INFRA-1 | Requires live Redis + process restart | Run `uvicorn app.main:app`, SIGTERM, check logs for "Redis pool closed" and no "Connection refused" errors |
| Todoist HMAC matches live webhook delivery | INFRA-4 | Requires real Todoist webhook delivery | Use Todoist webhook test delivery; check handler returns 200 and job appears in arq queue |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
