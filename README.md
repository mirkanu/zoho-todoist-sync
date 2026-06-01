# zoho-todoist-sync

> **Personal project:** This was built to solve a specific problem for the author. It works for that purpose. It has not been tested for general deployment and is not actively maintained — use it as inspiration or a starting point, not a supported tool.

> **100% AI-generated:** No code was written by hand. Every file was produced by [Claude Code](https://claude.ai/claude-code) via the [GSD workflow](https://github.com/pablof7z/gsd). The author is a non-programmer building personal tools with AI. PRs are welcome — if one arrives, Claude Code will review and merge it. Issues are unlikely to receive a response.

A two-way sync service between Zoho CRM tasks (assigned to you) and a single Todoist project. New Zoho tasks appear in Todoist within about a minute; completions, title changes, and due date changes flow in both directions. The hard problem with any two-way sync is the feedback loop: a change in system A triggers an update in system B, which triggers an update back in system A, indefinitely. This service solves that by computing a canonical hash of each task's key fields and ignoring any incoming change that matches the hash it last wrote — so echoed updates are dropped before they fan out, not after.

## Features

- **Zoho → Todoist:** New tasks and updates appear in Todoist within ~60 seconds via Zoho webhook
- **Todoist → Zoho:** Completions, title edits, and due date changes sync back on a 15-minute poll
- **Loop-safe:** Tracks a canonical hash per task; echoed changes are silently dropped
- **Priority mapping:** Zoho Highest/High/Normal/Low maps to Todoist p1–p4 correctly
- **Email notifications:** Resend delivers a digest when tasks are created or deleted
- **Self-hosted:** Runs as two Docker containers (web + worker) with Postgres and Redis

## Stack

Python 3.12 · FastAPI · arq · SQLAlchemy · PostgreSQL · Redis · Zoho CRM API · Todoist REST API · Resend

> **Tip:** Not sure where to start? Paste the link to this page into [Claude](https://claude.ai), [ChatGPT](https://chat.openai.com), or any AI assistant and ask it to walk you through the setup. These tools can read GitHub pages and guide you step by step.

## Quick Setup

1. **Clone the repo**
   ```bash
   git clone https://github.com/mirkanu/zoho-todoist-sync.git
   cd zoho-todoist-sync
   ```

2. **Create a Zoho OAuth app** at [api-console.zoho.eu](https://api-console.zoho.eu) (Server-based Apps). Note the Client ID and Client Secret, and generate a Refresh Token with `ZohoCRM.modules.tasks` + `ZohoCRM.settings.fields` scopes.

3. **Get a Todoist API token** from Todoist Settings → Integrations → Developer.

4. **Get a Resend API key** at [resend.com](https://resend.com) for email notifications (free tier is fine).

5. **Copy and fill in the env file**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials — see variable descriptions below
   ```

6. **Start the services**
   ```bash
   docker compose up -d
   ```

7. **Register the Zoho webhook** pointing at `https://your-domain/webhooks/zoho` for the Tasks module (create/edit/delete events).

### Environment variables

| Variable | Description |
|---|---|
| `ZOHO_CLIENT_ID` | OAuth app Client ID |
| `ZOHO_CLIENT_SECRET` | OAuth app Client Secret |
| `ZOHO_REFRESH_TOKEN` | OAuth Refresh Token (long-lived) |
| `ZOHO_USER_ID` | Your Zoho user ID |
| `ZOHO_REGION` | `eu` or `com` depending on your Zoho account region |
| `ZOHO_ORG_ID` | Your Zoho organisation ID (for task URL generation) |
| `TODOIST_API_TOKEN` | Todoist personal API token |
| `TODOIST_PROJECT_ID` | ID of the Todoist project to sync into |
| `TODOIST_CLIENT_SECRET` | Todoist app secret (used for webhook HMAC verification) |
| `RESEND_API_KEY` | Resend API key for email notifications |
| `DATABASE_URL` | Postgres connection string |
| `REDIS_URL` | Redis connection string |
