---
phase: 06-webhooks
reviewed: 2026-04-24T00:00:00Z
depth: standard
files_reviewed: 5
files_reviewed_list:
  - app/webhooks/__init__.py
  - app/webhooks/router.py
  - tests/unit/test_webhooks.py
  - app/main.py
  - tests/unit/test_main_lifespan.py
findings:
  critical: 0
  warning: 3
  info: 3
  total: 6
status: issues_found
---

# Phase 06: Code Review Report

**Reviewed:** 2026-04-24T00:00:00Z
**Depth:** standard
**Files Reviewed:** 5
**Status:** issues_found

## Summary

Reviewed the Phase 06 webhook implementation: the Zoho and Todoist webhook handlers, the app lifespan wiring in `app/main.py`, and both test suites. The HMAC verification, raw-body-first read order, constant-time comparison, project-ID gate, and loop-suppression logic are all correctly implemented. No critical security issues found.

Three warnings were identified: a background task leak during startup-sync failure, a confusing silent discard when `project_id` is absent from a Todoist payload, and a fragile relative-path read in a test. Three info items cover code quality and test reliability.

---

## Warnings

### WR-01: Background refresh task leaked when `startup_sync` raises

**File:** `app/main.py:95-109`

**Issue:** `refresh_task` is created at line 95 via `asyncio.create_task(...)`. If `startup_sync` raises at line 106, the exception is re-raised after calling `todoist_client.close()` — but `refresh_task` is never cancelled. The still-running background task holds a reference to `session_factory` and `token_state`, and will continue executing `proactive_refresh_loop` (attempting DB reads and token refreshes) until the process is torn down by the ASGI server. In Railway's deployment environment this is a short-lived race, but it can produce confusing log output and unnecessary Zoho API calls during a failed boot.

**Fix:**
```python
# After startup_sync raises, cancel the background task before re-raising.
try:
    await startup_sync(todoist_client, session_factory, settings)
except Exception:
    await todoist_client.close()
    refresh_task.cancel()
    try:
        await refresh_task
    except (asyncio.CancelledError, Exception):
        pass
    raise
```

---

### WR-02: Silent discard on missing `project_id` in Todoist payload

**File:** `app/webhooks/router.py:121`

**Issue:** The project isolation gate is:
```python
if event_data.get("project_id") != settings.todoist_project_id:
```
If a Todoist webhook payload omits `project_id` (e.g., a future API change or a non-standard event type), `event_data.get("project_id")` returns `None`, which is not equal to the configured project ID string, so the event is silently discarded with only a DEBUG log (`todoist_event_wrong_project`). A legitimate sync event with an accidentally missing `project_id` field would be silently dropped — difficult to diagnose. Additionally, if Todoist ever sends `project_id` as an integer, the string comparison would fail for the same reason.

**Fix:**
```python
raw_project_id = event_data.get("project_id")
if raw_project_id is None:
    log.warning(
        "todoist_event_missing_project_id",
        event_name=event_name,
        todoist_task_id=todoist_task_id,
    )
    return {"ok": True}
if str(raw_project_id) != str(settings.todoist_project_id):
    log.debug(
        "todoist_event_wrong_project",
        event_name=event_name,
        todoist_task_id=todoist_task_id,
        project_id=raw_project_id,
    )
    return {"ok": True}
```

---

### WR-03: Relative path in test reads source file — breaks when run from non-root directory

**File:** `tests/unit/test_webhooks.py:384`

**Issue:** `pathlib.Path("app/webhooks/router.py").read_text()` is a relative path. If `pytest` is invoked from any directory other than the project root (e.g., `cd tests && pytest`, or CI with a different working directory), this raises `FileNotFoundError` and the test fails with a confusing error instead of the intended assertion failure.

**Fix:**
```python
import pathlib

def test_todoist_hmac_compare_uses_compare_digest():
    src = (pathlib.Path(__file__).parents[2] / "app" / "webhooks" / "router.py").read_text()
    assert "hmac.compare_digest" in src, "Todoist HMAC must use hmac.compare_digest"
    forbidden = [
        "expected == received", "received == expected",
        "expected==received", "received==expected",
    ]
    for pattern in forbidden:
        assert pattern not in src, f"Found timing-vulnerable comparison: {pattern}"
```

---

## Info

### IN-01: `str(event_data.get("id", ""))` produces `"None"` when `id` is explicitly `null`

**File:** `app/webhooks/router.py:116`

**Issue:** `event_data.get("id", "")` returns `None` (not `""`) when the JSON payload contains `"id": null`, because `dict.get` only uses the default when the key is *absent* — not when the value is `None`. `str(None)` produces the string `"None"`, which is passed to `_lookup_zoho_id` and then to `enqueue_sync`. This does not cause a crash (the DB lookup returns nothing for `"None"`) but produces misleading logs.

**Fix:**
```python
todoist_task_id = str(event_data.get("id") or "")
```

---

### IN-02: `webhook_client` fixture mutates module-level `app.state` without teardown

**File:** `tests/unit/test_webhooks.py:36-40`

**Issue:** The `webhook_client` fixture sets `app.state.redis` and `app.state.session_factory` on the shared `app` singleton imported from `app.main`. These attributes are never cleared after each test. Several tests also re-assign `app.state.session_factory` directly inside the test body without using the fixture. While the autouse `_clear_settings_cache` fixture handles settings, there is no analogous cleanup for `app.state`. In the current test suite this is harmless because each test that uses `app.state.session_factory` re-assigns it before the relevant assertion. However, any future test that accidentally relies on a prior test's `session_factory` would see stale state.

**Fix:** Add teardown to the fixture:
```python
@pytest.fixture
def webhook_client(complete_env):
    from app.main import app
    app.state.redis = AsyncMock()
    app.state.session_factory = MagicMock()
    yield TestClient(app, raise_server_exceptions=True)
    # Teardown: clear injected state
    if hasattr(app.state, "redis"):
        del app.state.redis
    if hasattr(app.state, "session_factory"):
        del app.state.session_factory
```

---

### IN-03: Lifespan wiring test duplicated across two test files

**File:** `tests/unit/test_webhooks.py:211-285` and `tests/unit/test_main_lifespan.py`

**Issue:** `test_lifespan_wires_arq_redis_and_session_factory` in `test_webhooks.py` substantially duplicates the fixture setup and assertions already covered by `test_main_lifespan.py`. Maintaining two parallel lifespan test setups increases the cost of future lifespan changes. This test was presumably added as part of Phase 06 to verify ArqRedis wiring, but now that `test_main_lifespan.py` has full lifespan coverage, the duplicate in `test_webhooks.py` can be removed or replaced with a narrower assertion (e.g., only asserting `app.state.redis is mock_redis`).

**Fix:** Remove `test_lifespan_wires_arq_redis_and_session_factory` from `test_webhooks.py` and verify coverage is retained by `test_main_lifespan.py`'s existing `_patched_lifespan` fixture tests.

---

_Reviewed: 2026-04-24T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
