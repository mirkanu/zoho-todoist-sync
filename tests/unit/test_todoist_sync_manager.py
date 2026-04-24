# tests/unit/test_todoist_sync_manager.py
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.todoist.sync_manager import (
    KV_SYNC_TOKEN_KEY,
    load_sync_token,
    save_sync_token,
    startup_sync,
)


def test_kv_sync_token_key_constant():
    assert KV_SYNC_TOKEN_KEY == "todoist_sync_token"


@pytest.mark.asyncio
async def test_load_sync_token_missing_returns_wildcard():
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    result = await load_sync_token(session)
    assert result == "*"


@pytest.mark.asyncio
async def test_load_sync_token_present_returns_value():
    session = AsyncMock()
    row = MagicMock()
    row.value = "stored-token-xyz"
    session.get = AsyncMock(return_value=row)
    result = await load_sync_token(session)
    assert result == "stored-token-xyz"


@pytest.mark.asyncio
async def test_load_sync_token_empty_value_returns_wildcard():
    session = AsyncMock()
    row = MagicMock()
    row.value = ""
    session.get = AsyncMock(return_value=row)
    result = await load_sync_token(session)
    assert result == "*"


@pytest.mark.asyncio
async def test_save_sync_token_upserts_and_commits(monkeypatch):
    session = AsyncMock()
    session.commit = AsyncMock()
    upserts = []

    async def fake_upsert(s, k, v):
        upserts.append((k, v))

    monkeypatch.setattr("app.todoist.sync_manager.upsert_kv", fake_upsert)
    await save_sync_token(session, "new-token-abc")
    assert upserts == [("todoist_sync_token", "new-token-abc")]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_sync_full_on_missing_token(monkeypatch):
    # session_factory yielding async-context session with no stored token
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.get = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session_factory = MagicMock(return_value=session)

    todoist_client = MagicMock()
    todoist_client.fetch_sync_delta = AsyncMock(
        return_value=([], "fresh-sync-token")
    )

    settings = MagicMock()
    settings.todoist_project_id = "6gCPcWwM392GhXQh"

    upserts = []
    async def fake_upsert(s, k, v):
        upserts.append((k, v))
    monkeypatch.setattr("app.todoist.sync_manager.upsert_kv", fake_upsert)

    await startup_sync(todoist_client, session_factory, settings)

    # fetch_sync_delta called with '*' (full sync)
    todoist_client.fetch_sync_delta.assert_awaited_once_with(
        sync_token="*", project_id="6gCPcWwM392GhXQh"
    )
    # new token persisted
    assert upserts == [("todoist_sync_token", "fresh-sync-token")]


@pytest.mark.asyncio
async def test_startup_sync_incremental_on_stored_token(monkeypatch):
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    row = MagicMock()
    row.value = "stored-abc"
    session.get = AsyncMock(return_value=row)
    session.commit = AsyncMock()
    session_factory = MagicMock(return_value=session)

    todoist_client = MagicMock()
    todoist_client.fetch_sync_delta = AsyncMock(return_value=([], "new-xyz"))

    settings = MagicMock()
    settings.todoist_project_id = "proj-1"

    monkeypatch.setattr(
        "app.todoist.sync_manager.upsert_kv",
        AsyncMock(),
    )

    await startup_sync(todoist_client, session_factory, settings)
    todoist_client.fetch_sync_delta.assert_awaited_once_with(
        sync_token="stored-abc", project_id="proj-1"
    )


@pytest.mark.asyncio
async def test_startup_sync_discards_items_without_footer(monkeypatch):
    # structlog bypasses stdlib caplog; use a spy on log.info instead.
    from app.todoist import sync_manager as sm

    log_events = []
    def spy_log(event, **kw):
        log_events.append(event)
    monkeypatch.setattr(sm.log, "info", spy_log)

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.get = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session_factory = MagicMock(return_value=session)

    items = [
        {"id": "1", "description": "Has footer\n[zoho:111]", "is_deleted": False, "project_id": "p"},
        {"id": "2", "description": "No footer here", "is_deleted": False, "project_id": "p"},
        {"id": "3", "description": "", "is_deleted": False, "project_id": "p"},
        {"id": "4", "description": "[zoho:444]", "is_deleted": True, "project_id": "p"},
    ]
    todoist_client = MagicMock()
    todoist_client.fetch_sync_delta = AsyncMock(return_value=(items, "tok"))

    settings = MagicMock()
    settings.todoist_project_id = "p"

    monkeypatch.setattr(
        "app.todoist.sync_manager.upsert_kv",
        AsyncMock(),
    )

    await startup_sync(todoist_client, session_factory, settings)

    # Items 2 and 3 have no footer; item 4 is deleted; item 1 processed.
    assert "todoist_item_no_footer_discarded" in log_events
    assert "todoist_item_deleted_skipped" in log_events
    assert "todoist_startup_sync_complete" in log_events


@pytest.mark.asyncio
async def test_startup_sync_persists_token_before_processing(monkeypatch):
    """SEED-7 critical: new sync_token is saved BEFORE iterating items."""
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.get = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session_factory = MagicMock(return_value=session)

    todoist_client = MagicMock()
    items_with_footer = [
        {"id": "1", "description": "[zoho:111]", "is_deleted": False, "project_id": "p"},
    ]
    todoist_client.fetch_sync_delta = AsyncMock(
        return_value=(items_with_footer, "must-be-saved-first")
    )

    call_order = []
    async def fake_upsert(s, k, v):
        call_order.append(("upsert", k, v))
    monkeypatch.setattr("app.todoist.sync_manager.upsert_kv", fake_upsert)

    # Spy on extract_zoho_id to record when processing happens
    from app.todoist import sync_manager as sm
    original_extract = sm.extract_zoho_id
    def spy_extract(d):
        call_order.append(("extract", d))
        return original_extract(d)
    monkeypatch.setattr(sm, "extract_zoho_id", spy_extract)

    settings = MagicMock()
    settings.todoist_project_id = "p"

    await startup_sync(todoist_client, session_factory, settings)

    # Upsert (token save) must happen before any extract call (item processing)
    upsert_idx = next(i for i, c in enumerate(call_order) if c[0] == "upsert")
    extract_idx = next(i for i, c in enumerate(call_order) if c[0] == "extract")
    assert upsert_idx < extract_idx, (
        f"sync_token must be persisted BEFORE item processing. Got order: {call_order}"
    )
