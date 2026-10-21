# 2026-10-20 orphan_sweep incident — forensic record

This directory archives the one-off scripts and forensic data produced during recovery from the 2026-10-20 orphan_sweep incident.

**What happened:** `app/nirvana/client.py` (Phase 09, 2026-07-28) silently returned empty results because it parsed `result.tasks` (not the real `result.groups[].tasks[]`) and used offset/limit pagination (not cursor). `orphan_sweep`'s two-cycle confirmation treated lies as real and hard-deleted 76 Zoho task counterparts that had Nirvana-side links. 49 of those 76 had been intentionally completed by the owner; 2 were in Nirvana trash; 25 were still active and were re-linked to fresh Zoho tasks via the recovery scripts below.

**Recovered:** All 76 original Zoho IDs are gone (Recycle Bin restore requires the `recyclebin.ALL` OAuth scope, which the production refresh token does not hold). The 25 active Nirvana partners have new Zoho task IDs, re-linked via `sync_state` to the *existing* Nirvana UUIDs — no duplicate Nirvana tasks.

**Safeguards added (commits 4b633e4 / 6b26c71 / 122bee3):**
- `NirvanaClient.get_tasks()` walks the real wire shape (`result.groups[].tasks[]`).
- `get_tasks_paginated()` uses cursor pagination (not offset).
- `ZTS_DISABLE_ORPHAN_SWEEP=1` env kill-switch short-circuits orphan_sweep (still persists `orphan_sweep_last_run` for monitoring).
- Mixed-provider rows now require 3 missing cycles before deletion (matched-provider default unchanged at 2).
- First-cycle threshold alert (default 5 rows/sweep) emits `client_data_stale_sweep` to email + Telegram — catches the same pattern that caused this incident within one sweep instead of two.

**Re-enable checklist:** `ZTS_DISABLE_ORPHAN_SWEEP=` (empty) only after running `../live_nirvana_smoke.py` against the real Nirvana account and confirming at least one Nirvana row survives a full sweep cycle. See `../live_nirvana_smoke.py` usage.

## Files

| File | Purpose |
|------|---------|
| `audit_2026_10_20_orphan_deletions.py` | One-off audit script — produces `audit_2026_10_20.csv` |
| `audit_2026_10_20.csv` | Pre-recovery state of all 76 deleted sync_state rows |
| `deletion_emails_2026_10_20.csv` | "Task deleted by sync service" emails pulled from Resend log |
| `not_found_49_titles.txt` | Nirvana-side original Subjects for the 49 intentionally-completed tasks |
| `recover_2026_10_20_orphan_deletions.py` | Recovery driver — produces `recovery_inputs_2026_10_20.csv` |
| `recovery_inputs_2026_10_20.csv` | Per-row recovery inputs (title, due_date, has_email) |
| `recreate_25_zoho.py` | Recreates the 25 still-active rows in Zoho, links to existing Nirvana UUIDs |
| `nirvana_walker_2026_10_20.py` | Cursor-based Nirvana walker — independent of broken NirvanaClient, used to harvest `title` + `due_date` for the 76 deleted rows |
| `recovery_2026_10_20_summary.txt` | Run output of `recreate_25_zoho.py` (25/25 success) |

These are forensic records and one-off scripts — they are not part of the runtime and not invoked by `docker-compose`. The reusable live-API tests live at `../live_nirvana_smoke.py` and `../e2e_nirvana_test.py`.
