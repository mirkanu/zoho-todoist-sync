#!/usr/bin/env bash
# Run the live E2E sync test against the running Hetzner worker.
#
# Exercises the full bidirectional Zoho ↔ Nirvana pipeline end-to-end
# (create / update / complete / delete, both directions) using the actual
# sync code path — the script enqueues sync_task jobs to Redis and the
# running zoho-sync-worker consumes them. Cleans up after itself
# (best-effort via try/finally).
#
# This is the canonical "is the sync working?" sanity check. Run after
# any change to app/ and after rebuilding the worker image.
#
# IMPORTANT — what this does NOT cover: orphan_sweep.
# Passing this test does NOT justify lifting ZTS_DISABLE_ORPHAN_SWEEP.
# orphan_sweep needs its own separate dry-run against a non-empty
# sync_state before the kill-switch can be turned off.
#
# Exit 0 = all 7 propagation directions succeeded.
# Exit 1 = at least one direction failed (assertion or error printed).
#
# Usage:
#   ./scripts/run_live_e2e.sh
#
# No flags, no env overrides — uses whatever's in
# /home/services/.env.production at run time.

set -euo pipefail

cd /home/services/hetzner-vps

timeout 600 docker compose run --rm --no-deps \
  -e PYTHONPATH=/app \
  -v /home/services/zoho-todoist-sync/scripts:/work \
  zoho-sync-worker \
  python /work/e2e_nirvana_test.py
