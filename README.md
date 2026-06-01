# zoho-todoist-sync

> **Personal project:** This was built to solve a specific problem for the author. It works for that purpose. It has not been tested for general deployment and is not actively maintained — use it as inspiration or a starting point, not a supported tool.

> **100% AI-generated:** No code was written by hand. Every file was produced by [Claude Code](https://claude.ai/claude-code) via the [GSD workflow](https://github.com/pablof7z/gsd). The author is a non-programmer building personal tools with AI. PRs are welcome — if one arrives, Claude Code will review and merge it. Issues are unlikely to receive a response.

A two-way sync service between Zoho CRM tasks (assigned to the current user) and a single Todoist project. Runs as a background service. No UI — just reliable, automatic task propagation with enriched descriptions and loop-safe conflict resolution.

## What It Does

- Zoho CRM tasks assigned to you appear in Todoist within ~60 seconds
- Edits (title, due date, completion) flow both ways without creating infinite loops
- Replaces fragile Make.com / Zapier scenarios with a self-hosted worker

## Stack

Python · Zoho CRM API · Todoist REST API · self-hosted on a VPS

## Setup

This project is not packaged for general distribution. If you want to adapt it, start from the source and configure your own Zoho OAuth credentials and Todoist API token via environment variables. See the source for required env var names.
