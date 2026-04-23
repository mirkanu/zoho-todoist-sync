---
phase: 02-zoho-read
fixed_at: 2026-04-23T22:24:26Z
review_path: .planning/phases/02-zoho-read/02-REVIEW.md
iteration: 1
findings_in_scope: 4
fixed: 4
skipped: 0
status: all_fixed
---

# Phase 02: Code Review Fix Report

**Fixed at:** 2026-04-23T22:24:26Z
**Source review:** .planning/phases/02-zoho-read/02-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 4
- Fixed: 4
- Skipped: 0

## Fixed Issues

### WR-01: Expired stored token with `None` expires_at is not refreshed

**Files modified:** `app/zoho/token_manager.py`, `app/main.py`
**Commit:** 352ea38
**Applied fix:** Removed `await session.commit()` from inside `upsert_kv` and added a docstring note that callers are responsible for committing. Added `await session.commit()` after both `upsert_kv` calls in `app/main.py` (lifespan) and in `proactive_refresh_loop` in `token_manager.py`, so the two KV writes (token + expiry) are committed atomically in a single transaction.

### WR-02: `fetch_tasks_modified_since` propagates Zoho API errors without recording partial results

**Files modified:** `app/zoho/client.py`
**Commit:** 5f4f219
**Applied fix:** Added a NOTE paragraph to the `fetch_tasks_modified_since` docstring explicitly stating that the method is all-or-nothing — if any page request fails the exception propagates and already-accumulated records are discarded, so callers must retry the full call on `ZohoRateLimitError` / `ZohoAPIError`.

### WR-03: `test_proactive_refresh_loop_reraises_on_failure` does not verify token_state is unchanged after failure

**Files modified:** `tests/unit/test_token_manager.py`
**Commit:** 248141c
**Applied fix:** Added `assert token_state == {}` immediately after the `pytest.raises` block in `test_proactive_refresh_loop_reraises_on_failure`, ensuring a future regression that mutates state before raising will be caught.

### WR-04: `test_lifespan_logs_warn_when_terminal_status_missing_from_picklist` assertion is fragile

**Files modified:** `tests/unit/test_main_lifespan.py`
**Commit:** 933651a
**Applied fix:** Replaced both fragile `rec.__dict__` wide-text-search assertions with targeted checks. Each test now filters `caplog.records` to WARNING-level records and asserts `any("event_key" in r.getMessage() for r in warn_records)`, eliminating false positives from unrelated log record attributes.

---

_Fixed: 2026-04-23T22:24:26Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
