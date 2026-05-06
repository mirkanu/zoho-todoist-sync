"""Unit tests for backfill_descriptions script logic (DESC-6)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.todoist.description import ZOHO_TASK_BASE_URL
from app.zoho.client import ZohoNotFoundError
from scripts.backfill_descriptions import backfill_one


# ---------------------------------------------------------------------------
# Test 1: Skip task that already has the Zoho link in description
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_skips_task_with_existing_zoho_link():
    """Idempotency: task whose description already contains Zoho link is skipped."""
    todoist_task = MagicMock()
    todoist_task.description = f"{ZOHO_TASK_BASE_URL}/Z111 — some text"

    todoist_api = AsyncMock()
    todoist_api.get_task = AsyncMock(return_value=todoist_task)

    zoho_client = AsyncMock()

    counters = {"skipped": 0, "updated": 0, "not_found": 0, "errors": 0}
    await backfill_one("Z111", "T111", zoho_client, todoist_api, dry_run=False, counters=counters)

    todoist_api.update_task.assert_not_called()
    assert counters["skipped"] == 1


# ---------------------------------------------------------------------------
# Test 2: Update task that has no description
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_updates_task_without_description():
    """Task with no description is fetched from Zoho and description written to Todoist."""
    todoist_task = MagicMock()
    todoist_task.description = None

    todoist_api = AsyncMock()
    todoist_api.get_task = AsyncMock(return_value=todoist_task)
    todoist_api.update_task = AsyncMock()

    zoho_client = AsyncMock()
    zoho_client.get_task = AsyncMock(
        return_value={"data": [{"id": "Z222", "What_Id": {"name": "Acme Deal", "id": "DEAL1"}}]}
    )

    counters = {"skipped": 0, "updated": 0, "not_found": 0, "errors": 0}
    await backfill_one("Z222", "T222", zoho_client, todoist_api, dry_run=False, counters=counters)

    todoist_api.update_task.assert_called_once()
    call_kwargs = todoist_api.update_task.call_args
    # Verify task_id and description content
    assert call_kwargs.args[0] == "T222" or call_kwargs.kwargs.get("task_id") == "T222"
    desc = call_kwargs.kwargs.get("description", "")
    assert "Acme Deal" in desc
    assert f"{ZOHO_TASK_BASE_URL}/Z222" in desc
    assert counters["updated"] == 1


# ---------------------------------------------------------------------------
# Test 3: Handle ZohoNotFoundError gracefully
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_handles_zoho_not_found():
    """Task missing from Zoho API is logged and skipped — loop continues."""
    todoist_task = MagicMock()
    todoist_task.description = None

    todoist_api = AsyncMock()
    todoist_api.get_task = AsyncMock(return_value=todoist_task)
    todoist_api.update_task = AsyncMock()

    zoho_client = AsyncMock()
    zoho_client.get_task = AsyncMock(side_effect=ZohoNotFoundError("404"))

    counters = {"skipped": 0, "updated": 0, "not_found": 0, "errors": 0}
    await backfill_one("Z333", "T333", zoho_client, todoist_api, dry_run=False, counters=counters)

    todoist_api.update_task.assert_not_called()
    assert counters["not_found"] == 1


# ---------------------------------------------------------------------------
# Test 4: Dry-run does not write to Todoist but increments updated counter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_dry_run_does_not_write():
    """Dry-run mode: description is built but update_task is NOT called; counter incremented."""
    todoist_task = MagicMock()
    todoist_task.description = None

    todoist_api = AsyncMock()
    todoist_api.get_task = AsyncMock(return_value=todoist_task)
    todoist_api.update_task = AsyncMock()

    zoho_client = AsyncMock()
    zoho_client.get_task = AsyncMock(
        return_value={"data": [{"id": "Z444", "What_Id": None}]}
    )

    counters = {"skipped": 0, "updated": 0, "not_found": 0, "errors": 0}
    await backfill_one("Z444", "T444", zoho_client, todoist_api, dry_run=True, counters=counters)

    todoist_api.update_task.assert_not_called()
    assert counters["updated"] == 1
