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
async def test_startup_sync_counts_deleted_and_processed(monkeypatch):
    from app.todoist import sync_manager as sm

    log_events = []
    def spy_log(event, **kw):
        log_events.append((event, kw))
    monkeypatch.setattr(sm.log, "info", spy_log)

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.get = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session_factory = MagicMock(return_value=session)

    items = [
        {"id": "1", "description": "Some task", "is_deleted": False, "project_id": "p"},
        {"id": "2", "description": "Another task", "is_deleted": False, "project_id": "p"},
        {"id": "3", "description": "", "is_deleted": True, "project_id": "p"},
    ]
    todoist_client = MagicMock()
    todoist_client.fetch_sync_delta = AsyncMock(return_value=(items, "tok"))

    settings = MagicMock()
    settings.todoist_project_id = "p"

    monkeypatch.setattr("app.todoist.sync_manager.upsert_kv", AsyncMock())

    await startup_sync(todoist_client, session_factory, settings)

    complete = next((kw for ev, kw in log_events if ev == "todoist_startup_sync_complete"), None)
    assert complete is not None
    assert complete["total"] == 3
    assert complete["processed"] == 2
    assert complete["deleted"] == 1


@pytest.mark.asyncio
async def test_startup_sync_persists_token_before_processing(monkeypatch):
    """SEED-7 critical: new sync_token is saved BEFORE iterating items."""
    from app.todoist import sync_manager as sm

    call_order = []
    async def fake_upsert(s, k, v):
        call_order.append(("upsert", k, v))
    monkeypatch.setattr("app.todoist.sync_manager.upsert_kv", fake_upsert)

    # Spy on log.info to detect when item processing completes
    original_log_info = sm.log.info
    def spy_log(event, **kw):
        if event == "todoist_startup_sync_complete":
            call_order.append(("complete", event))
        original_log_info(event, **kw)
    monkeypatch.setattr(sm.log, "info", spy_log)

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.get = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session_factory = MagicMock(return_value=session)

    todoist_client = MagicMock()
    todoist_client.fetch_sync_delta = AsyncMock(
        return_value=([{"id": "1", "is_deleted": False}], "must-be-saved-first")
    )

    settings = MagicMock()
    settings.todoist_project_id = "p"

    await startup_sync(todoist_client, session_factory, settings)

    # Upsert (token save) must happen before item processing completes
    upsert_idx = next(i for i, c in enumerate(call_order) if c[0] == "upsert")
    complete_idx = next(i for i, c in enumerate(call_order) if c[0] == "complete")
    assert upsert_idx < complete_idx, (
        f"sync_token must be persisted BEFORE item processing. Got order: {call_order}"
    )
