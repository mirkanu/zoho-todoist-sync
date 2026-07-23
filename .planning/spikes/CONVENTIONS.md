# Spike Conventions

Patterns and stack choices established across spike sessions. New spikes follow these unless the question requires otherwise.

## Stack

- Python 3.12, matching the main project. Each spike that needs newer dependencies than the pinned main app gets its own isolated `venv/` inside the spike directory (system Python here is externally-managed; don't `pip install --break-system-packages`).
- No web UI — these were pure fact-finding spikes (auth works? what shape? do writes persist?), verified via stdout/JSON, per the spike workflow's guidance to skip UI for binary yes/no and data-shape questions.

## Structure

- One spike per numbered directory (`NNN-descriptive-name/`), each with `spike.py` + `README.md`.
- Later spikes reuse the venv from spike 001 (`../001-nirvana-mcp-auth-headless/venv/bin/python`) rather than creating a new one each time.

## Patterns

- Secrets (`NIRVANA_PAT`) live in `/home/services/.env.production`, loaded per-spike-run via `export NIRVANA_PAT=$(grep -m1 '^NIRVANA_PAT=' ... | cut -d= -f2-)` — the shared file has other values with unescaped spaces that break a plain `source`, so targeted `grep` is safer than sourcing the whole file.
- When an MCP tool's call shape isn't documented publicly, pull the real `inputSchema` via `session.list_tools()` rather than guessing from a sibling tool's shape.
- Verify writes via a separate `get_tasks`/read call, not by trusting the mutation response echo.
- Clean up any test data created against a live account (soft-delete / trash) at the end of a write spike.

## Tools & Libraries

- `mcp` Python SDK: pin ≥1.28.1 for `mcp.client.streamable_http` support (the version already used by the main app, 1.6.0, predates that module).
