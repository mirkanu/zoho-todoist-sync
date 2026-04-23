# zoho-todoist-sync

Two-way sync between Zoho CRM Tasks (assigned to me) and a Todoist project. Runs as two Railway services (`web` + `worker`). No UI.

## Stack

- Python 3.12 + FastAPI + arq + Postgres + Redis on Railway
- `todoist-api-python`, Zoho official Python SDK
- Resend for email notifications

## Key Facts

- **Zoho org region**: EU (`crm.zoho.eu`, `www.zohoapis.eu`, `accounts.zoho.eu`)
- **Todoist project ID**: `6gCPcWwM392GhXQh` ("Zoho (synced!) Make.com")
- **Zoho custom field**: `Todoist Task ID` — already exists, already populated by Make.com. Exact `api_name` must be fetched from Zoho settings at startup (likely `Todoist_Task_ID` but verify).
- **ID linkage in Todoist**: Footer in task description: `\n\n---\n[zoho:{ZOHO_TASK_ID}]`
- **Loop prevention**: Canonical hash of `{title, due_date (date-only), priority (Todoist int 1–4), is_completed}`. Echo = incoming hash matches stored hash → skip.

## Critical Constraints

- **Priority mapping is NOT inverted**: Zoho Highest→Todoist 4 (p1/urgent), not 1. Highest→4, High→3, Normal→2, Low/unset→1.
- **Due date always date-only**: Always normalise to `YYYY-MM-DD`. Never pass `due_datetime` to Todoist.
- **Description sync is OUT of v1**: Todoist description used only for `[zoho:ID]` footer.
- **Zoho webhook is notification-only**: Payload contains `module` + `ids` only. Worker MUST fetch full task from API on dequeue.
- **Migration is NOT a fresh seed**: Existing Todoist tasks (from Make.com) must be linked by ID via `Todoist_Task_ID` Zoho field. Run E2E test before migration.

## Planning

All planning artifacts in `.planning/`. Workflow: `/gsd-plan-phase`, `/gsd-execute-phase`.

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
