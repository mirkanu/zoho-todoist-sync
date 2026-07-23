# Project State: zoho-todoist-sync

**Current milestone:** v1.1 Nirvana Provider
**Current phase:** Phase 9 — Nirvana TaskProvider (not planned yet)

## Accumulated Context

v1.0 (Phases 1-8) shipped 2026-05-01: two-way Zoho CRM Tasks ↔ Todoist sync running on Hetzner VPS. See `CLAUDE.md` for architecture, deployment, and critical constraints — the original STATE.md/ROADMAP.md history was purged from git for containing infra-specific details (commit 991b280).

Phase 9 is informed by three validated spikes in `.planning/spikes/` (001-003): Nirvana's MCP server can be driven headlessly via a personal access token, task data shape confirmed (state + starred as independent axes, open-vocabulary state field), and writes (create/update/complete/trash) confirmed to persist. A REST wrapper (`POST mcp.nirvanahq.com/playground/run/:tool`) was also confirmed working — simpler than the full MCP SDK/session protocol.

### Roadmap Evolution

- Phase 9 added: Nirvana TaskProvider — replace Todoist with Nirvana as the sync target behind a TaskProvider abstraction, based on validated spikes 001-003
