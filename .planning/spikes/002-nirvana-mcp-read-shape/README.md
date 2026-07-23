---
spike: 002
name: nirvana-mcp-read-shape
type: standard
validates: "Given a connected MCP session, when tasks are read back, then the actual JSON shape (GTD state, Focus flag, due date, project/tags) is captured and compared to docs"
verdict: VALIDATED
related: [001-nirvana-mcp-auth-headless]
tags: [mcp, nirvana, data-model]
---

# Spike 002: Nirvana MCP Task Data Shape

## What This Validates

The priority-mapping design for a Nirvana `TaskProvider` depends on knowing exactly what fields come back from `get_tasks` — this spike reads real data from the account (validated in spike 001) to confirm the actual shape, not just what the docs describe.

## How to Run

```bash
cd .planning/spikes/002-nirvana-mcp-read-shape
export NIRVANA_PAT=$(grep -m1 '^NIRVANA_PAT=' /home/services/.env.production | cut -d= -f2-)
../001-nirvana-mcp-auth-headless/venv/bin/python spike.py
```

## What to Expect

Real task JSON from the live account, queried across several state filters and the starred flag.

## Investigation Trail

1. Queried `get_tasks` with `state=next` — confirms baseline shape: `id`, `name`, `type` (`task` or `project`), `state`, `starred` (bool), `tags` (array), `created`, `last_modified_days`. `duedate` only present when the task has one (sparse field, not `null`).
2. Tried `state=focus` (matching the `focus` key in `get_task_counts`) — **failed with `400 Invalid state: focus`**. Confirms Focus is not a GTD state at all — it's the boolean `starred` field layered on top of whatever real state the task is in. Correct filter is `starred: true`.
3. Confirmed via `starred: true` — returned a task in `state: "scheduled"` with `starred: true`. So "Focus" in the Nirvana UI = starred flag, independent of GTD state. Also revealed `parent` (nested object: id/name/state/tags of the containing project) and `startdate` distinct from `duedate`.
4. Queried `state=waiting` — adds a `waitingfor` string field (freetext, e.g. who/what you're waiting on) not present on other states.
5. Queried `state=scheduled` — **surfaced an undocumented state value**: one result had `state: "recurring"` even though it was returned by the `scheduled` filter. Recurring tasks appear to carry their own state distinct from one-off scheduled tasks. Not listed in `get_task_counts`' enumerated keys (`inbox, next, waiting, scheduled, someday, later, focus, projects, reflists`) — `recurring` and `trash` both exist as real per-task `state` values that don't map 1:1 to that summary list.
6. Queried `state=someday` — returned items with `type: "project"`, confirming projects and tasks share the same `state` vocabulary and the same `get_tasks` endpoint (distinguished only by `type`).
7. Queried `state=inbox` — plain unprocessed capture, no `duedate`/`tags` beyond default.

## Results

**Verdict: VALIDATED**, with one meaningful correction to the pre-spike assumption:

- **Confirmed fields relevant to sync:** `id`, `name`, `state`, `starred`, `tags[]`, `duedate` (sparse), `startdate` (sparse, distinct from due date), `parent` (project linkage), `waitingfor` (state-specific), `type` (task vs project).
- **Correction:** "Focus" is not a GTD state to branch on — it's the `starred` boolean, orthogonal to `state`. Any priority-mapping design (Zoho picklist → Nirvana) should treat `starred` and `state` as two independent axes, not try to fold Focus into the state enum.
- **New unknown surfaced:** `state` has at least one value (`recurring`) not listed in `get_task_counts`'s summary keys. The real `state` vocabulary needs to be treated as open/unenumerated rather than assumed to match the counts endpoint — worth defensive handling (unknown states shouldn't crash the sync) in the real build.
- No `completed`/`done` state was sampled directly in this pass (none were surfacing near the top of any queried list) — the `update_tasks` tool docs from spike 001 confirm `completed: true` is how completion is set, so this is a write-side concern for spike 003, not a gap here.

**Impact on remaining spikes:** proceed to 003 (write roundtrip). No assumption was invalidated seriously enough to stop — just corrected (Focus = starred, not a state).
