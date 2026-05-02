# Roadmap: zoho-todoist-sync

## v1.0 — Shipped 2026-05-01

Loop-safe two-way Zoho CRM ↔ Todoist sync: 8 phases, 174 commits, 42 tasks migrated from Make.com. [Full archive](.planning/milestones/v1.0-ROADMAP.md)

---

## v1.1 — Planning

*Next milestone to be defined via `/gsd-new-milestone`.*

### Candidate items

- OBS-2: Fix `sync_events.source` enum (`zoho_webhook`/`todoist_webhook`/`reconciler`/`migration` instead of `worker`)
- Resend verified sender domain (emails currently non-deliverable with `sync-alerts@resend.dev`)
- Formally re-run Phase 07 missed-webhook E2E test
- V2: Include Zoho task link + Deal title in Todoist content (user-requested from Make.com)
