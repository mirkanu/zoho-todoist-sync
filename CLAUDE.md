# zoho-todoist-sync

Two-way sync between Zoho CRM Tasks (assigned to me) and a Todoist project. Runs as two Hetzner Docker services (`web` + `worker`). No UI.

## Stack

- Python 3.12 + FastAPI + arq + Postgres + Redis on Hetzner VPS (migrated from Railway, 2026-04-25)
- `todoist-api-python`, Zoho official Python SDK
- Resend for email notifications

## Deployment

Migrated from Railway to Hetzner VPS on 2026-04-25.

| | |
|---|---|
| **Host** | Hetzner VPS — `hetzner-vps` (37.27.212.18) |
| **Web container** | `zoho-sync-web`, port 3003 on host |
| **Worker container** | `zoho-sync-worker` (no exposed port) |
| **Redis** | `zoho-sync-redis` container |
| **Database** | `zoho-sync-db` PostgreSQL container |
| **Compose file** | `/home/services/hetzner-vps/docker-compose.yml` |
| **Railway** | No longer used |

## Key Facts

- **Zoho org region**: EU (`crm.zoho.eu`, `www.zohoapis.eu`, `accounts.zoho.eu`)
- **Todoist project ID**: `6gCPcWwM392GhXQh` ("Zoho (synced!) Make.com")
- **Zoho custom field**: `Todoist Task ID` — already exists, already populated by Make.com. Exact `api_name` must be fetched from Zoho settings at startup (likely `Todoist_Task_ID` but verify).
- **ID linkage**: Bidirectional via `sync_state` DB table (zoho_task_id ↔ todoist_task_id) + Zoho custom field `Todoist_Task_ID`. No description footer — that approach was decided against and never implemented.
- **Loop prevention**: Canonical hash of `{title, due_date (date-only), priority (Todoist int 1–4), is_completed}`. Echo = incoming hash matches stored hash → skip.

## Critical Constraints

- **Priority mapping is NOT inverted**: Zoho Highest→Todoist 4 (p1/urgent), not 1. Highest→4, High→3, Normal→2, Low/unset→1.
- **Due date always date-only**: Always normalise to `YYYY-MM-DD`. Never pass `due_datetime` to Todoist.
- **Description sync is OUT of v1**: Todoist description not written (v1.1 will add Zoho link + deal context).
- **Zoho webhook is notification-only**: Payload contains `module` + `ids` only. Worker MUST fetch full task from API on dequeue.
- **Migration is NOT a fresh seed**: Existing Todoist tasks (from Make.com) must be linked by ID via `Todoist_Task_ID` Zoho field. Run E2E test before migration.

## Planning

All planning artifacts in `.planning/`. Workflow: `/gsd-plan-phase`, `/gsd-execute-phase`.

After every plan execution completes, run `/gsd-verify-work` before reporting done — do not wait for a dashboard trigger.

---

## Verbosity Contract

These rules apply to every terminal session in this project. They reduce what Claude says in the terminal so the tmux pane stays readable.

1. **Skip CONTEXT.md interrogation when CONTEXT.md already exists.** If `.planning/phases/{phase}/{phase}-CONTEXT.md` is present, do not re-interview the user about the phase — proceed directly to planning.
2. **Name the phase in plain English in the first line of the session report.** Instead of "I will now begin Phase 56", write "Starting CLI Verbosity Contract + Portfolio Feed work." One line, present tense, specific.
3. **Don't repeat what the user just said.** If the user said "plan phase 56", do not echo back "You asked me to plan phase 56." Begin the work.
4. **Prefer one-line status updates.** Instead of a paragraph explaining what you are about to do, emit a single line: "Reading roadmap." "Writing plan 01." "Done." Reserve multi-line output for actual results (lists of tasks, file paths, errors).
5. **Active voice, present tense.** Write "Creating feedStore.js" not "feedStore.js will be created" and not "I am in the process of creating feedStore.js".

<!-- GSD:non-programmer-contract-start source:templates/claude-md.md -->
## Non-Programmer Contract

Claude must never ask the user to perform a programmer action that Claude can do itself. Technical decisions are made by Claude using its own judgment, documented in the session report, and reversible by the user in plain English.

| Forbidden | Replacement |
|-----------|-------------|
| Asking user to open/view/read code | Read it yourself; summarise findings in plain English |
| Asking user to paste git diffs or logs | Read them yourself with `git diff`, `git log`, or file reads |
| Asking user to edit a config/.env/any file | Edit it yourself; use the Global Env Editor (Dashboard) if credentials are missing |
| Asking user to run a terminal command | Run it yourself |
| "Deploy started, check back in a few minutes" | Run the deploy, wait for it, verify it's live, then ping the user |
| Asking user to run the tests | Run them yourself; only report after they pass (or after a real failure needing a decision) |
| Asking user a technical architecture decision in jargon | Decide yourself; state the decision in plain English; offer to change course |
| Asking user to review code before commit | Commit yourself after verify-work passes |
| "You'll need to do X manually after this finishes" | Don't finish until X is done, or add X to the plan |
| "I'll leave this for you to configure" | Configure with a sensible default; document in the session report |
| Technical disambiguation questions mid-plan | Use CLAUDE.md defaults; only escalate if truly stuck, framed in plain English |
| Asking user to paste an API key in the terminal | Use the Global Env Editor panel (Dashboard) |
<!-- GSD:non-programmer-contract-end -->

- **REQUIREMENTS.md** — 37 requirements with stable REQ-IDs
- **ROADMAP.md** — 8 phases
- **STATE.md** — current position

## Environment Variables (all required)

```
ZOHO_CLIENT_ID
ZOHO_CLIENT_SECRET
ZOHO_REFRESH_TOKEN
ZOHO_USER_ID
ZOHO_REGION=eu
ZOHO_TODOIST_TASK_ID_FIELD   # resolved at startup from Zoho settings
ZOHO_TERMINAL_STATUSES=Completed
ZOHO_JOB_DEFER_SECS=2
TODOIST_API_TOKEN
TODOIST_PROJECT_ID=6gCPcWwM392GhXQh
TODOIST_CLIENT_SECRET        # for HMAC webhook verification
RESEND_API_KEY
DATABASE_URL
REDIS_URL
LOG_LEVEL=INFO
```

## Open Questions (resolve in Phase 1–2)

1. Exact `api_name` of `Todoist_Task_ID` custom field (fetch from `GET /crm/v6/settings/fields?module=Tasks`)
2. Actual Zoho Status picklist values for this org
3. Raw `Due_Date` format from this org (date-only or datetime?)
4. Zoho webhook payload for deletion vs. reassignment events
5. Todoist Sync API project filtering (server-side or client-side?)
6. arq cron `"*/15 * * * *"` syntax support in pinned version
7. Zoho `criteria` parameter syntax for v6 API `Modified_Time` filter
