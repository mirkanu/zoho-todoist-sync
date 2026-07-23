# Spike Manifest

## Idea

Validate whether Nirvana (nirvanahq.com) can replace Todoist as the sync target for zoho-todoist-sync, built behind a `TaskProvider` abstraction so switching back to Todoist is a redeploy, not a rewrite. Nirvana has no official REST API or webhooks — the only sanctioned integration point is its native MCP server. These spikes de-risk the assumptions behind that plan before any real build work starts.

## Requirements

- Provider abstraction (`TaskProvider` interface) selected via env var (e.g. `TASK_PROVIDER`), not a hardcoded rewrite.
- Nirvana Pro required (write operations via MCP are Pro-gated) — user already has this.
- Hourly polling is acceptable staleness for Nirvana-side changes (no webhook equivalent exists).
- Auth for the backend worker should use Nirvana's long-lived personal access token (PAT) for headless/custom clients, not the interactive OAuth flow meant for AI-assistant clients (Claude Desktop, ChatGPT, etc.) — confirmed to exist via `mcp.nirvanahq.com/developers`, per the MCP setup docs at `nirvanahq.com/help/ai/mcp-setup`.
- "Focus" is the `starred` boolean field, independent of `state` — priority mapping must treat them as two separate axes, not fold Focus into the GTD state enum.
- `state` is an open/unenumerated vocabulary (e.g. `recurring` exists as a real per-task state not listed in `get_task_counts`'s summary keys) — the real build must handle unknown state values defensively rather than assuming a fixed enum.
- `update_tasks` takes a top-level `updates` array (not `tasks`), each item flat with `id` plus changed fields (full replacement for `tags`).
- Nirvana's `completed` field reads back as a date string when set, not a boolean — a Nirvana provider must derive `is_completed = completed is not None` for the canonical-hash loop-prevention logic, unlike Todoist's native boolean.
- The main app's pinned `mcp` SDK (1.6.0) lacks `mcp.client.streamable_http` — needs bumping to ≥1.8ish (spike used 1.28.1) for the real build.

## Spikes

| # | Name | Type | Validates | Verdict | Tags |
|---|------|------|-----------|---------|------|
| 001 | nirvana-mcp-auth-headless | standard | Given a Nirvana PAT, when a Python script (not an interactive AI client) connects to `mcp.nirvanahq.com/mcp`, then it authenticates and stays connected without a human present | ✓ VALIDATED | mcp, auth, nirvana |
| 002 | nirvana-mcp-read-shape | standard | Given a connected MCP session, when tasks are read back, then the actual JSON shape (GTD state, Focus flag, due date, project/tags) is captured and compared to docs | ✓ VALIDATED | mcp, nirvana, data-model |
| 003 | nirvana-mcp-write-roundtrip | standard | Given a Pro account and a test task, when the sync worker calls update/complete/retag via MCP, then the change persists and is readable back | ✓ VALIDATED | mcp, nirvana, write |
