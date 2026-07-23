---
title: Nirvana as Todoist alternative — research findings
date: 2026-07-23
context: Explored replacing Todoist with Nirvana (nirvanahq.com) as the sync target, with an easy switch-back to Todoist.
---

## Decision direction

Replace Todoist with Nirvana as the active sync target, but build it as a swap, not a rewrite: a `TaskProvider` abstraction that both Todoist and Nirvana implement, selected via a single env var (e.g. `TASK_PROVIDER=nirvana`). Switching back to Todoist should be a redeploy, not a code change.

## Research findings (2026-07-23)

- **No official public REST API.** Nirvana announced a developer API years ago but never shipped it publicly. Existing docs (community wiki, Ruby gem, Python library) are reverse-engineered from the web app's internal calls to `api.nirvanahq.com` — unofficial, unsupported, can break without notice.
- **No webhooks.** No push-notification mechanism found, official or community. Any integration has to poll.
- **Official native MCP server exists** at `nirvanahq.com/help/ai/mcp-setup` — a real first-party integration point. Supports read (tasks by GTD state, project, tag, date range) and write (rename, retag, complete, star, move lists, delete). **Write operations require Nirvana Pro** — free accounts are read + create only. User confirmed they're already on Pro.
- **Auth is OAuth** — short-lived access token + refresh token via Nirvana's sign-in screen. This is MCP-specific auth, not a pasteable API key like Todoist's.
- **Priority model is fundamentally different from Todoist's p1–p4.** Nirvana uses GTD states (Next/Later/Waiting/Scheduled/Someday) plus a binary "Focus" star flag — not a graded scale. Mapping Zoho priority → Nirvana will be lossy/heuristic, unlike the current direct Zoho-priority → Todoist-int mapping.
- **No rate-limit or reliability data found** for either the unofficial REST API or the MCP server — unverified.

## Constraints agreed with user

- **Polling interval:** even hourly is acceptable staleness for Nirvana-side changes (no webhook equivalent, so this must be polled). Much more relaxed than the current Todoist webhook's near-real-time behavior.
- **Nirvana Pro required** for write access via MCP — user already has this.

## Open risk

The MCP server's behavior has only been verified from documentation, not against a live account from a headless/backend worker process (as opposed to interactive AI-assistant use). See [[nirvana-mcp-server-validation]] spike.
