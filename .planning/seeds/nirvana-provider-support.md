---
title: Nirvana provider support via TaskProvider abstraction
trigger_condition: After the nirvana-mcp-server-validation spike confirms the Nirvana MCP server can be driven reliably from a backend/worker process (OAuth token refresh, read + write, GTD state and Focus flag data) — not just interactive AI-assistant use.
planted_date: 2026-07-23
---

## Idea

Replace Todoist with Nirvana (nirvanahq.com) as the active sync target for zoho-todoist-sync, built as a swap rather than a rewrite:

- Introduce a `TaskProvider` interface that both the existing Todoist integration and a new Nirvana integration implement.
- Select the active provider via a single env var (e.g. `TASK_PROVIDER=todoist` / `TASK_PROVIDER=nirvana`). Switching back to Todoist is a redeploy, not a code change.
- Nirvana integration goes through its official MCP server (OAuth-based), the only sanctioned integration point — no official REST API, no webhooks.
- Sync loop polls Nirvana on an interval (hourly is acceptable staleness, per user) instead of reacting to webhooks like the current Zoho/Todoist flow.
- Priority mapping needs rework: Zoho picklist → Nirvana GTD state (Next/Later/Waiting/Scheduled/Someday) + binary Focus flag, replacing the current direct Zoho-priority → Todoist p1–p4 int mapping. This will be lossy/heuristic by nature.
- Requires Nirvana Pro (user already has this) since MCP write operations are Pro-gated.

See [[nirvana-as-todoist-alternative-research]] for full research findings and rationale.
