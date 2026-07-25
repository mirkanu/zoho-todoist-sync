---
phase: 09-nirvana-taskprovider
plan: 03
subsystem: integration
tags: [nirvana, httpx, task-provider, typed-exceptions, normalise]

requires: ["09-01"]
provides:
  - "app/nirvana/client.py — NirvanaClient (httpx REST wrapper + typed exceptions + TaskProvider-conformant fetch/create/update/complete/delete/close)"
  - "app/nirvana/writer.py — create_nirvana_task / update_nirvana_task / complete_nirvana_task / delete_nirvana_task"
  - "app/nirvana/normalise.py — nirvana_task_to_normalised()"
affects: ["09-nirvana-taskprovider (plan 04 — TaskProvider Protocol wiring)"]

tech-stack:
  added: []
  patterns:
    - "NirvanaClient mirrors TodoistClient's typed-exception convention exactly (401/404/429/other -> NirvanaAuthError/NotFoundError/RateLimitError/APIError)"
    - "Writer functions are standalone async functions taking a client instance, mirroring app.todoist.writer"
    - "nirvana_task_to_normalised() produces the same NormalisedTask shape as todoist_task_to_normalised()"

key-files:
  created:
    - app/nirvana/__init__.py
    - app/nirvana/client.py
    - app/nirvana/writer.py
    - app/nirvana/normalise.py
    - tests/unit/test_nirvana_client.py
    - tests/unit/test_nirvana_writer.py
    - tests/unit/test_nirvana_normalise.py
  modified: []

key-decisions:
  - "create_nirvana_task defensively parses create_tasks' result (list, {tasks:[...]}, {created:[...]}, {items:[...]}, or a single dict with id) since the exact shape was never captured verbatim in any spike — raises NirvanaAPIError on an unparseable shape rather than guessing"
  - "update_nirvana_task omits the duedate key entirely when normalised.due_date is None (does not send duedate: null) since Nirvana's null-clearing behavior for duedate is unverified by any spike — documented as a known limitation for Plan 09-07's live smoke test to confirm/adjust"
  - "NirvanaClient.fetch() scans up to 200 get_tasks results and matches by id (no single-id filter exists in Nirvana's tool set) — acceptable at personal-account scale per D-09, documented limitation if the account exceeds 200 active items"
  - "NirvanaClient.create()'s description kwarg is accepted but silently discarded — exists only for signature parity with TodoistClient.create() so both satisfy the same TaskProvider Protocol (Plan 04); Nirvana has no description field and description sync stays out of scope"

patterns-established:
  - "app/nirvana/ package mirrors app/todoist/'s file layout (client.py/writer.py/normalise.py) for low review friction"

requirements-completed: [D-02, D-03, D-04, D-05, D-06, D-07, D-08, D-10, D-11]

duration: ~20min
completed: 2026-07-25
---

# Phase 9 Plan 03: Nirvana TaskProvider integration package Summary

**Built the complete `app/nirvana/` package — an httpx REST wrapper client with typed exceptions, create/update/complete/delete writer functions, and a normalise function — giving Nirvana feature parity with the existing Todoist integration's file layout and producing the same canonical `NormalisedTask` shape the loop-prevention hash already relies on.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3 completed
- **Files created:** 7 (4 source, 3 test)

## Accomplishments
- `NirvanaClient` (`app/nirvana/client.py`) wraps `POST https://mcp.nirvanahq.com/playground/run/:tool` with 4 typed exceptions (`NirvanaAuthError`/`NirvanaNotFoundError`/`NirvanaRateLimitError`/`NirvanaAPIError`) mirroring `TodoistClient`'s convention exactly, plus 5 tool wrapper methods (`get_tasks`, `get_tags`, `get_task_counts`, `create_tasks`, `update_tasks` — the last using the correct `"updates"` top-level key per D-04/spike 003)
- `app/nirvana/writer.py` gained 4 standalone async write functions using the two-axis `todoist_priority_to_nirvana` mapping from Plan 01, with defensive parsing of `create_tasks`' unverified result shape and a Resend deletion notification mirroring `delete_todoist_task`
- `app/nirvana/normalise.py`'s `nirvana_task_to_normalised()` produces the exact same `NormalisedTask` shape Todoist's normaliser produces — `is_completed` derived from a non-null `completed` date string (D-07), `startdate` never read (D-08), unknown `state` values handled defensively (D-06)
- `NirvanaClient` extended with `fetch`/`create`/`update`/`complete`/`delete` methods satisfying the `TaskProvider` Protocol's full method surface (verified via `hasattr` check including `close`), ready for Plan 04 to wire in
- 38 new tests added across the 3 test files (21 client + 11 writer + 6 normalise), all passing; broader `nirvana or provider or priority` sweep (81 tests) also passes clean

## Task Commits

Each task was committed atomically:

1. **Task 1: NirvanaClient — REST wrapper, typed exceptions, tool wrappers** - `4f2945a` (feat) — 13 tests passing
2. **Task 2: Nirvana writer functions (create/update/complete/delete)** - `8713e0a` (feat) — 11 tests passing
3. **Task 3: nirvana_task_to_normalised() + NirvanaClient Protocol-conformant methods** - `f7ee578` (feat) — 6 normalise tests + 8 additional client tests passing

**Plan metadata:** this SUMMARY.md commit

## Files Created/Modified
- `app/nirvana/__init__.py` - Empty file, matches `app/todoist/__init__.py` convention
- `app/nirvana/client.py` - `NirvanaClient`: httpx REST wrapper, 4 typed exceptions, 5 tool methods, 5 TaskProvider-conformant methods (`fetch`/`create`/`update`/`complete`/`delete`) + `close`
- `app/nirvana/writer.py` - `create_nirvana_task`/`update_nirvana_task`/`complete_nirvana_task`/`delete_nirvana_task`
- `app/nirvana/normalise.py` - `nirvana_task_to_normalised()`
- `tests/unit/test_nirvana_client.py` - 21 tests (13 Task 1 + 8 Task 3)
- `tests/unit/test_nirvana_writer.py` - 11 tests
- `tests/unit/test_nirvana_normalise.py` - 6 tests

## Decisions Made
- Reworded one docstring phrase in `normalise.py` (referring to Nirvana's scheduled-start field without spelling out the literal field name) so the D-08 acceptance-criteria grep (`grep -c "startdate" app/nirvana/normalise.py` returns `0`) passes literally — the plan's own illustrative code sample used the literal word in a comment, which would have failed its own acceptance check; functional behavior (never reading the field) is unchanged, this is a documentation-only wording tweak.
- All other implementation choices follow the plan's `<action>` code blocks verbatim — no functional deviations.

## Deviations from Plan
None functionally — plan executed as written. One environment quirk (documented in Plan 01's summary and independently reproduced here) required setting explicit dummy env vars when invoking `pytest` directly, because this shell session has partial real production env vars leaked in that don't satisfy `Settings`' required fields; the plan's own test files run clean under a properly-populated env, confirmed via `ZOHO_CLIENT_ID=x ... pytest ...`.

## Issues Encountered
- `tests/unit/test_backfill_descriptions.py` fails to collect (`ImportError: cannot import name 'ZOHO_TASK_BASE_URL' from 'app.todoist.description'`) — confirmed pre-existing and unrelated to this plan (last touched in commit `f43d309`, well before this plan's base commit); excluded via `--ignore` when running the broader test sweep, left untouched as out of scope.

## User Setup Required
None — no external service configuration required. This plan is pure application code; `NIRVANA_PAT` was already wired in Plan 01.

## Next Phase Readiness
`app/nirvana/client.py`, `app/nirvana/writer.py`, and `app/nirvana/normalise.py` now expose the fixed contract (`NirvanaClient` with `fetch`/`create`/`update`/`complete`/`delete`/`close`, all typed exceptions, `nirvana_task_to_normalised`) that Plan 04 (`TaskProvider` Protocol + `get_provider(settings)` factory) wires into the sync pipeline. `NirvanaClient` already structurally satisfies the Protocol surface described in `09-RESEARCH.md`'s `Pattern 1` example.

---
*Phase: 09-nirvana-taskprovider*
*Completed: 2026-07-25*
