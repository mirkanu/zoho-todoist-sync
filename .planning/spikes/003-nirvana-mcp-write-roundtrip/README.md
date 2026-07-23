---
spike: 003
name: nirvana-mcp-write-roundtrip
type: standard
validates: "Given a Pro account and a test task, when the sync worker calls update/complete/retag via MCP, then the change persists and is readable back"
verdict: VALIDATED
related: [001-nirvana-mcp-auth-headless, 002-nirvana-mcp-read-shape]
tags: [mcp, nirvana, write]
---

# Spike 003: Nirvana MCP Write Roundtrip

## What This Validates

Write access via MCP is Pro-gated and is the capability the entire sync depends on (Zoho → Nirvana pushes need create + update; Nirvana → Zoho needs to detect real changes). This spike proves writes actually persist, not just that the call returns `ok: true`.

## How to Run

```bash
cd .planning/spikes/003-nirvana-mcp-write-roundtrip
export NIRVANA_PAT=$(grep -m1 '^NIRVANA_PAT=' /home/services/.env.production | cut -d= -f2-)
../001-nirvana-mcp-auth-headless/venv/bin/python spike.py
```

Creates one throwaway task (`ZTS-SPIKE-003 ...`), mutates it through several states, verifies via a separate `get_tasks` read, then soft-deletes (moves to trash) as cleanup.

## Investigation Trail

1. First attempt at `update_tasks` used `{"tasks": [...]}` (guessed from `create_tasks`' shape) — failed with `400 update must be an object`. Pulled the actual `inputSchema` from `list_tools()` rather than continuing to guess: the correct top-level key is **`updates`**, not `tasks`, and each item's fields are flat (no nested `update` object) — the error message was slightly misleading.
2. This first failed attempt still successfully ran `create_tasks` before the update calls failed, leaving an orphaned test task in the account. Caught it in the second `get_tasks` verification (`total: 2` when only 1 was expected) and cleaned it up manually after the spike — a reminder that partial-failure cleanup matters for the real sync worker too (create succeeding independently of subsequent update calls).
3. Re-ran with `updates` key: `duedate`, `starred`, and `tags` (full-replacement, not additive — matches the tool description's warning) all persisted and read back correctly via a separate `get_tasks` call (not just trusting the `update_tasks` response echo).
4. Moved `state: "waiting"` with `waitingfor` — persisted correctly, `waitingfor` field appeared as documented.
5. Set `completed: true` — **the persisted value is not a boolean.** `get_tasks` afterward shows `"completed": "2026-07-23"` (a date string), not `true`. The write request accepts a bool but the read-back representation is "the date it was completed." This matters directly for this project's loop-prevention hash, which currently treats `is_completed` as a boolean derived from Todoist's own boolean field — a Nirvana provider needs to derive `is_completed = completed is not None`, not compare types directly.
6. Cleaned up via `state: "trash"` (soft-delete, matches `update_tasks` docs) — confirmed by re-reading, task shows `state: "trash"` with all other fields (starred, tags, duedate, completed) intact, i.e. trash doesn't clear task data. Good — no accidental data loss risk if the sync worker trashes something during testing.

## Results

**Verdict: VALIDATED.** Full write path works: create → update (multiple fields, multiple calls) → complete → soft-delete, each step independently confirmed via a fresh read rather than trusting the mutation response. No write silently failed or reverted.

**Correction for the real build:** `completed` reads back as a date string, not a boolean — the canonical-hash loop-prevention logic (`{title, due_date, priority, is_completed}` per CLAUDE.md) needs `is_completed = task.get("completed") is not None` for a Nirvana provider, not a direct boolean field like Todoist's `is_completed`.

**Impact:** no assumption was invalidated. All three spikes in this series (auth, read shape, write roundtrip) now VALIDATED — feasibility for a Nirvana `TaskProvider` is confirmed, with three corrections captured for the real build (see MANIFEST.md Requirements).
