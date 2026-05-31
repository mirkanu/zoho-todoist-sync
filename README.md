# zoho-todoist-sync

> **Personal project:** This was built for the author's own use and has not been tested or optimised for deployment by others. It is shared in the hope it may be useful — no support is implied.

> **Note:** This project was written entirely using [Claude Code](https://claude.ai/claude-code) and the [GSD workflow](https://github.com/pablof7z/gsd). No code was written by hand.

A two-way sync service between Zoho CRM tasks (assigned to the current user) and a single Todoist project. Runs as a background service. No UI — just reliable, automatic task propagation with enriched descriptions and loop-safe conflict resolution.

## What It Does

- Zoho CRM tasks assigned to you appear in Todoist within ~60 seconds
- Edits (title, due date, completion) flow both ways without creating infinite loops
- Replaces fragile Make.com / Zapier scenarios with a self-hosted worker

## Stack

Python · Zoho CRM API · Todoist REST API · self-hosted on a VPS

## Setup

This project is not packaged for general distribution. If you want to adapt it, start from the source and configure your own Zoho OAuth credentials and Todoist API token via environment variables. See the source for required env var names.
