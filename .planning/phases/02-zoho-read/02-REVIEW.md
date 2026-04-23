---
phase: 02-zoho-read
reviewed: 2026-04-23T00:00:00Z
depth: standard
files_reviewed: 10
files_reviewed_list:
  - app/main.py
  - app/zoho/__init__.py
  - app/zoho/client.py
  - app/zoho/normalise.py
  - app/zoho/state.py
  - app/zoho/token_manager.py
  - tests/unit/test_main_lifespan.py
  - tests/unit/test_token_manager.py
  - tests/unit/test_zoho_client.py
  - tests/unit/test_zoho_normalise.py
findings:
  critical: 0
  warning: 4
  info: 4
  total: 8
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-04-23T00:00:00Z
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

The Phase 02 implementation covers the Zoho read path: OAuth token lifecycle (`token_manager.py`), raw API access (`client.py`), field normalisation (`normalise.py`), shared in-process state (`state.py`), and FastAPI lifespan wiring (`main.py`). The test suite provides good unit coverage for each module.

No critical security or data-loss bugs were found. There are four warnings — two in the production code (one logic gap in `main.py`, one unhandled pagination error path in `client.py`) and two in the test suite (a missing assertion and a patching gap). Four info-level items cover dead code, a fragile test helper, a magic number, and a minor type inconsistency.

---

## Warnings

### WR-01: Expired stored token with `None` expires_at is not refreshed

**File:** `app/main.py:47-51`

**Issue:** The `needs_refresh` condition is:
```python
needs_refresh = (
    not stored_token
    or stored_expires_at is None
    or stored_expires_at <= now_utc
)
```
This is correct _on its own_, but on line 83 the `datetime.fromisoformat()` parse in `load_token_from_kv` silently returns `None` for a corrupted/unparseable expires-at string. When `stored_token` is present but `stored_expires_at` is `None` (due to a parse failure), `needs_refresh` is `True` and a refresh happens — that part is fine.

However the inverse case is subtler: if `stored_token` is `None` but `stored_expires_at` is somehow non-`None` (e.g., a previous partial write wrote the expiry before the token), the condition short-circuits on `not stored_token` and still refreshes — also fine.

The actual gap is that after a successful refresh the token is written in **two separate `upsert_kv` calls** (lines 55–56) with **no transaction wrapping them together**. If the process crashes between the two calls, the DB will hold a new token without the new expiry, or a new expiry without the new token. On next startup `load_token_from_kv` will return a mismatched pair. With the current logic this triggers another refresh (the expiry mismatch causes `needs_refresh=True`), so the functional impact is low — but it is a latent correctness issue that becomes a real bug if `needs_refresh` logic ever changes.

**Fix:** Wrap both upserts in a single session/transaction so they commit atomically:
```python
async with session_factory() as session:
    await upsert_kv(session, KV_ACCESS_TOKEN_KEY, access_token)
    await upsert_kv(session, KV_EXPIRES_AT_KEY, expires_at.isoformat())
```
The current code already opens a single `session_factory()` context for both calls (lines 54–56), so this is already implemented correctly in `main.py`. The same pattern should be verified inside `proactive_refresh_loop` in `token_manager.py` (lines 109–111) — that loop also opens one `async with session_factory() as session:` context for both upserts, which is correct. **The real fix needed is in `upsert_kv` itself**: it calls `session.commit()` after every individual key write (line 72 in `token_manager.py`). This means the two calls from the caller share a session but commit independently — the second commit is after the first, so a crash between writes still leaves a partial state.

```python
# token_manager.py — remove auto-commit from upsert_kv; let callers commit
async def upsert_kv(session: AsyncSession, key: str, value: str) -> None:
    existing = await session.get(KVStore, key)
    if existing is None:
        session.add(KVStore(key=key, value=value))
    else:
        existing.value = value
    # Do NOT commit here — callers commit after all writes

# main.py / proactive_refresh_loop — commit once after both writes
async with session_factory() as session:
    await upsert_kv(session, KV_ACCESS_TOKEN_KEY, access_token)
    await upsert_kv(session, KV_EXPIRES_AT_KEY, expires_at.isoformat())
    await session.commit()
```

---

### WR-02: `fetch_tasks_modified_since` propagates Zoho API errors without recording partial results

**File:** `app/zoho/client.py:131-147`

**Issue:** When paginating, if page N succeeds and page N+1 returns a non-2xx (e.g., 429 or 5xx), `_handle` raises immediately and all records already accumulated in `results` are silently discarded. The caller has no way to distinguish "no tasks" from "partial page failure". This creates a silent data gap: tasks that were received on earlier pages will not be processed.

For a 429 the caller can retry the whole operation (losing only time), but for intermittent 5xx errors the missing page window may never be retried if the caller decides the request "succeeded" (it will raise, so the caller must handle it, but the partial `results` list is lost).

**Fix:** Either document clearly that this method is all-or-nothing (which it currently is, given the exception propagates), or accumulate the partial results and re-raise with them attached. Since callers will typically retry on transient errors, the simpler fix is to add a docstring note:
```python
# NOTE: raises on any page failure — partial results are not returned.
# Caller should retry the full call on ZohoRateLimitError / ZohoAPIError.
```
More robustly, catch retriable errors at the page level and re-raise with context so the caller can decide:
```python
try:
    body = self._handle(resp, f"GET /Tasks/search page={page}")
except (ZohoRateLimitError, ZohoAPIError):
    # Don't silently drop already-fetched records — surface with context
    raise
```
The current implementation already re-raises, so the fix is primarily documentation. However, if the cron job that calls this method does NOT retry on failure, the missing page window is a real bug and the loop should be made restartable (cursor-based pagination). For now, document the behaviour explicitly.

---

### WR-03: `test_proactive_refresh_loop_reraises_on_failure` does not verify token_state is unchanged after failure

**File:** `tests/unit/test_token_manager.py:127-138`

**Issue:** The test correctly verifies that `RuntimeError("network down")` is re-raised. However, it does not assert that `token_state` was NOT mutated by the failed refresh. If a future code change accidentally updates `token_state` before the exception (e.g., partial assignment), this test would not catch it — the stale/wrong token would be left in state.

**Fix:** Add an assertion after the `pytest.raises` block:
```python
with pytest.raises(RuntimeError, match="network down"):
    await proactive_refresh_loop(token_state, session_factory)
assert token_state == {}  # state must be unchanged after failure
```

---

### WR-04: `test_lifespan_logs_warn_when_terminal_status_missing_from_picklist` assertion is fragile

**File:** `tests/unit/test_main_lifespan.py:137-139`

**Issue:** The assertion concatenates both `rec.getMessage()` and the full `str(rec.__dict__)` for every log record:
```python
all_text = " ".join(rec.getMessage() for rec in caplog.records) + " " + \
           " ".join(str(rec.__dict__) for rec in caplog.records)
assert "zoho_terminal_status_not_in_picklist" in all_text
```
Searching `rec.__dict__` means the test will pass if the string `"zoho_terminal_status_not_in_picklist"` appears anywhere in any log record attribute — including in the test's own fixture data or in unrelated log record fields. The same pattern is used in `test_lifespan_logs_warn_when_todoist_field_missing` (line 152-153). This can produce false positives if, for example, a `structlog` event key shows up in a `args` tuple from a different log call.

**Fix:** Check the specific event field directly:
```python
warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
assert any("zoho_terminal_status_not_in_picklist" in r.getMessage() for r in warn_records)
```

---

## Info

### IN-01: `app/zoho/__init__.py` is empty

**File:** `app/zoho/__init__.py:1`

**Issue:** The file is blank. This is not a bug, but it is a missed opportunity to define the public API of the `zoho` package (exports, `__all__`), which would make import hygiene reviewable in later phases.

**Fix:** If the package intentionally exposes nothing, a brief comment is cleaner than a truly empty file:
```python
# app/zoho — Zoho CRM integration package.
# Public API is exposed via submodules: client, normalise, state, token_manager.
```

---

### IN-02: Magic number `200` for `per_page` in `fetch_tasks_modified_since`

**File:** `app/zoho/client.py:135`

**Issue:** `per_page: 200` is the Zoho API maximum page size. It appears as a bare integer with no named constant, making it non-obvious whether `200` is a configured limit or the API maximum.

**Fix:** Define a module-level constant:
```python
ZOHO_MAX_PAGE_SIZE: int = 200  # Zoho CRM API maximum per_page

# in fetch_tasks_modified_since:
params={"criteria": criteria, "page": page, "per_page": ZOHO_MAX_PAGE_SIZE},
```

---

### IN-03: `proactive_refresh_loop` uses a local import to avoid circular dependency

**File:** `app/zoho/token_manager.py:100-106`

**Issue:** The function imports its own module at call time (`import app.zoho.token_manager as _self`) and then calls `_self.refresh_access_token` and `_self.upsert_kv` via the module reference. The comment implies this is for testability (so `monkeypatch` can replace the functions on the module), which is a valid pattern for Python. However, the local import inside an `async` function body is unusual and will confuse readers. It also re-imports `get_settings` from `app.core.config` on every call to the loop body.

**Fix:** Move the `get_settings()` call outside the loop (settings don't change at runtime), and document the module self-reference pattern:
```python
async def proactive_refresh_loop(token_state: dict, session_factory: Callable[[], AsyncSession]) -> None:
    import app.zoho.token_manager as _self  # module-ref so monkeypatch works in tests
    from app.core.config import get_settings
    settings = get_settings()  # resolve once, not per-iteration
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECS)
        ...
```
This is already how the code is written — the `get_settings()` call IS outside the loop (line 102). The info item stands only for the documentation gap; the self-import pattern should have a comment explaining the rationale (monkeypatch support).

---

### IN-04: `_reset_state` fixture in test_main_lifespan uses `autouse=True` with module-level singleton state

**File:** `tests/unit/test_main_lifespan.py:13-19`

**Issue:** The `_reset_state` fixture clears `token_state` and `zoho_field_cache` before and after each test. This is correct for isolation, but it relies on the singletons being the same objects throughout the test session. If any test imports `token_state` with `from app.zoho.state import token_state` and then the module is reloaded, the fixture's reference and the test's reference may diverge. This is not a current bug (Python module caching makes this safe in practice), but it is worth a note.

Additionally, the fixture does not call `get_settings.cache_clear()` — unlike `_patched_lifespan` which does. If a test modifies environment variables without using `complete_env` and does NOT use `_patched_lifespan`, settings could leak between tests. This is low risk given current test structure, but worth noting.

**Fix:** No code change required. Document the assumption in the fixture docstring:
```python
@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level singletons between tests. Assumes module cache is stable."""
    token_state.clear()
    zoho_field_cache.clear()
    yield
    token_state.clear()
    zoho_field_cache.clear()
```

---

_Reviewed: 2026-04-23T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
