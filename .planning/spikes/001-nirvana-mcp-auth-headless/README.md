---
spike: 001
name: nirvana-mcp-auth-headless
type: standard
validates: "Given a Nirvana personal access token, when a headless Python script (not an interactive AI client) connects to mcp.nirvanahq.com/mcp, then it authenticates and stays connected without a human present"
verdict: VALIDATED
related: []
tags: [mcp, auth, nirvana]
---

# Spike 001: Nirvana MCP Headless Auth

## What This Validates

The biggest risk to "Nirvana as a Todoist replacement" was whether MCP — designed primarily for interactive AI-assistant clients (Claude Desktop, ChatGPT) with a human-in-the-loop OAuth login — could be driven by an unattended backend worker at all. If not, the whole sync-service model breaks.

## Research

Docs at `nirvanahq.com/help/ai/mcp-setup` mention two auth paths: interactive OAuth (short-lived access token + refresh token, for AI-assistant clients) and **personal access tokens (PATs) — "long-lived keys for clients without OAuth"** — available at `mcp.nirvanahq.com/developers`. The dev portal itself requires interactive sign-in to view/generate a PAT (the one step that had to be done by the account owner, not by this spike). User generated a PAT and provided it once; everything after that ran unattended.

MCP server endpoint: `https://mcp.nirvanahq.com/mcp` (streamable HTTP transport). Project's pinned `mcp` SDK (1.6.0, used by the main app) doesn't include `mcp.client.streamable_http` — that module was added in a later SDK release. Spike used an isolated venv with `mcp==1.28.1` to get streamable HTTP client support with custom headers.

## How to Run

```bash
cd .planning/spikes/001-nirvana-mcp-auth-headless
python3 -m venv venv && ./venv/bin/pip install mcp httpx
export NIRVANA_PAT=$(grep -m1 '^NIRVANA_PAT=' /home/services/.env.production | cut -d= -f2-)
./venv/bin/python spike.py
```

## What to Expect

Session initializes against the live MCP server using only `Authorization: Bearer <PAT>` — no browser, no OAuth redirect, no human interaction. Server reports 5 exposed tools: `get_tasks`, `get_tags`, `get_task_counts`, `create_tasks`, `update_tasks`.

## Investigation Trail

1. First attempt used the system-installed `mcp==1.6.0` (already present for the Zoho/Todoist sync app) — failed immediately, `mcp.client.streamable_http` doesn't exist in that version. Rather than upgrade the main app's pinned dependency for a throwaway spike, built an isolated venv (`mcp==1.28.1`) scoped to the spike directory only.
2. First live connection: `session.initialize()` succeeded on the first try with just a Bearer header — no OAuth handshake, no consent screen, no per-session re-auth. This is the PAT path, distinct from the interactive OAuth flow the docs describe for AI-assistant clients.
3. Ran `get_task_counts` twice, as two fully independent process invocations (fresh connection each time, same PAT), to rule out the token being single-use or tied to a specific session. Both runs returned identical live counts (178 next actions, 37 projects, etc.) — confirms the PAT is a genuinely reusable, long-lived credential suitable for a recurring polling worker.
4. Did not test token expiry/revocation behavior (would require waiting out an unknown TTL, or revoking mid-spike) — that's a residual unknown, but doesn't block scoping since a `401` on an expired PAT is a normal, handleable failure mode for a worker (same pattern as Zoho's refresh-token expiry today).

## Results

**Verdict: VALIDATED.** Nirvana's MCP server can be driven entirely headlessly via a personal access token — no OAuth, no browser, no human in the loop after the one-time PAT generation. This directly de-risks the core assumption behind building a Nirvana `TaskProvider`: a background worker can hold a static credential (same shape as `TODOIST_API_TOKEN` today) and call the MCP server on a polling interval, exactly like the rest of this project's architecture.

**Note for the real build:** the main app's pinned `mcp` SDK version will need to be bumped (or a second, spike-validated version pinned) to get `mcp.client.streamable_http`. Not a blocker, just a dependency update to carry into the real implementation.

**Impact on remaining spikes:** proceed to 002 (data shape) and 003 (write roundtrip) — no reason to stop here.
