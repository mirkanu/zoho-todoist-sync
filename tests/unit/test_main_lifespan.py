# tests/unit/test_main_lifespan.py
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.zoho.state import token_state, zoho_field_cache


@pytest.fixture(autouse=True)
def _reset_state():
    token_state.clear()
    zoho_field_cache.clear()
    yield
    token_state.clear()
    zoho_field_cache.clear()


@pytest.fixture
def _patched_lifespan(monkeypatch, complete_env):
    """Patch out engine creation, session factory, and Zoho HTTP to isolate lifespan logic."""
    from app.core.config import get_settings
    get_settings.cache_clear()

    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    monkeypatch.setattr("app.main.create_async_engine", lambda *a, **k: fake_engine)

    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None
    fake_session_factory = MagicMock(return_value=fake_session)
    monkeypatch.setattr("app.main.async_sessionmaker", lambda *a, **k: fake_session_factory)

    # Default: no stored token.
    load_mock = AsyncMock(return_value=(None, None))
    monkeypatch.setattr("app.main.load_token_from_kv", load_mock)

    refresh_mock = AsyncMock(
        return_value=("fresh_token_1000", datetime.now(timezone.utc) + timedelta(seconds=3600))
    )
    monkeypatch.setattr("app.main.refresh_access_token", refresh_mock)

    upsert_mock = AsyncMock()
    monkeypatch.setattr("app.main.upsert_kv", upsert_mock)

    # Field metadata mock (default: everything resolves cleanly).
    meta_mock = AsyncMock(return_value={
        "todoist_task_id_api_name": "Todoist_Task_ID",
        "status_picklist_values": ["Not Started", "Completed"],
    })
    class FakeClient:
        def __init__(self, access_token): self.access_token = access_token
        async def get_fields_metadata(self, module="Tasks"):
            return await meta_mock(module)
    monkeypatch.setattr("app.main.ZohoClient", FakeClient)

    # No-op refresh loop (so create_task doesn't actually loop forever).
    async def fake_loop(ts, sf):
        await asyncio.sleep(3600)   # sleeps until cancelled
    monkeypatch.setattr("app.main.proactive_refresh_loop", fake_loop)

    # Stub provider wiring so existing tests aren't broken by Task 2 additions.
    class FakeTaskProvider:
        def __init__(self, api_token=None):
            self.api_token = api_token
        async def close(self):
            pass
    fake_provider_instance = FakeTaskProvider(api_token="fake-token")
    monkeypatch.setattr("app.main.get_provider", lambda settings: fake_provider_instance)

    async def fake_startup_sync(client, factory, settings):
        pass
    monkeypatch.setattr("app.main.startup_sync", fake_startup_sync)

    # Stub ArqRedis pool creation so tests don't attempt a real Redis connection.
    fake_redis = AsyncMock()
    fake_redis.aclose = AsyncMock()
    fake_create_pool = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr("app.main.create_pool", fake_create_pool)

    return {
        "load_mock": load_mock,
        "refresh_mock": refresh_mock,
        "upsert_mock": upsert_mock,
        "meta_mock": meta_mock,
        "session_factory": fake_session_factory,
    }


async def test_lifespan_refreshes_when_token_missing(_patched_lifespan):
    from app.main import lifespan
    app = FastAPI()
    async with lifespan(app):
        assert token_state["access_token"] == "fresh_token_1000"
        assert zoho_field_cache["todoist_task_id_api_name"] == "Todoist_Task_ID"
    _patched_lifespan["refresh_mock"].assert_awaited_once()


async def test_lifespan_loads_token_from_kv_when_valid(_patched_lifespan):
    future = datetime.now(timezone.utc) + timedelta(seconds=1800)
    _patched_lifespan["load_mock"].return_value = ("stored_token_xyz", future)
    from app.main import lifespan
    app = FastAPI()
    async with lifespan(app):
        assert token_state["access_token"] == "stored_token_xyz"
    # Did NOT refresh because the stored token was valid.
    _patched_lifespan["refresh_mock"].assert_not_awaited()


async def test_lifespan_refreshes_when_token_expired(_patched_lifespan):
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    _patched_lifespan["load_mock"].return_value = ("expired_token", past)
    from app.main import lifespan
    app = FastAPI()
    async with lifespan(app):
        assert token_state["access_token"] == "fresh_token_1000"
    _patched_lifespan["refresh_mock"].assert_awaited_once()


async def test_lifespan_resolves_field_metadata_and_caches(_patched_lifespan):
    from app.main import lifespan
    app = FastAPI()
    async with lifespan(app):
        assert zoho_field_cache["todoist_task_id_api_name"] == "Todoist_Task_ID"
        assert "Completed" in zoho_field_cache["status_picklist_values"]


async def test_lifespan_starts_refresh_task(_patched_lifespan):
    from app.main import lifespan
    app = FastAPI()
    async with lifespan(app):
        assert hasattr(app.state, "zoho_refresh_task")
        assert isinstance(app.state.zoho_refresh_task, asyncio.Task)
        assert not app.state.zoho_refresh_task.done()
    # After exit, task should be cancelled.
    assert app.state.zoho_refresh_task.cancelled() or app.state.zoho_refresh_task.done()


async def test_lifespan_logs_warn_when_terminal_status_missing_from_picklist(
    _patched_lifespan, caplog
):
    _patched_lifespan["meta_mock"].return_value = {
        "todoist_task_id_api_name": "Todoist_Task_ID",
        "status_picklist_values": ["Not Started", "In Progress"],  # no "Completed"
    }
    from app.main import lifespan
    app = FastAPI()
    with caplog.at_level(logging.WARNING):
        async with lifespan(app):
            pass
    # The WARN must have been logged with event name zoho_terminal_status_not_in_picklist.
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("zoho_terminal_status_not_in_picklist" in r.getMessage() for r in warn_records)


async def test_lifespan_logs_warn_when_todoist_field_missing(_patched_lifespan, caplog):
    _patched_lifespan["meta_mock"].return_value = {
        "todoist_task_id_api_name": None,
        "status_picklist_values": ["Completed"],
    }
    from app.main import lifespan
    app = FastAPI()
    with caplog.at_level(logging.WARNING):
        async with lifespan(app):
            pass
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("zoho_todoist_task_id_field_not_found" in r.getMessage() for r in warn_records)


@pytest.mark.asyncio
async def test_lifespan_initialises_todoist_client_and_runs_startup_sync(
    monkeypatch, complete_env
):
    """get_provider() is called and startup_sync runs inside lifespan startup
    when TASK_PROVIDER=todoist (the complete_env default)."""
    from app.core.config import get_settings
    get_settings.cache_clear()

    # Stub every external side effect
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    monkeypatch.setattr("app.main.create_async_engine", lambda *a, **k: fake_engine)

    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None
    fake_session_factory = MagicMock(return_value=fake_session)
    monkeypatch.setattr("app.main.async_sessionmaker", lambda *a, **k: fake_session_factory)

    monkeypatch.setattr("app.main.refresh_access_token", AsyncMock(
        return_value=("tok", datetime.now(timezone.utc))
    ))
    monkeypatch.setattr("app.main.load_token_from_kv", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr("app.main.upsert_kv", AsyncMock())

    meta_mock = AsyncMock(return_value={
        "todoist_task_id_api_name": "Todoist_Task_ID",
        "status_picklist_values": ["Completed"],
    })
    class FakeZohoClient:
        def __init__(self, access_token): pass
        async def get_fields_metadata(self, module="Tasks"):
            return await meta_mock(module)
    monkeypatch.setattr("app.main.ZohoClient", FakeZohoClient)

    async def fake_loop(ts, sf):
        await asyncio.sleep(3600)
    monkeypatch.setattr("app.main.proactive_refresh_loop", fake_loop)

    # Track provider wiring
    provider_constructions = []
    class FakeTaskProvider:
        def __init__(self):
            self.closed = False
        async def close(self):
            self.closed = True

    fake_provider = FakeTaskProvider()
    def fake_get_provider(settings):
        provider_constructions.append(settings)
        return fake_provider
    monkeypatch.setattr("app.main.get_provider", fake_get_provider)

    startup_sync_calls = []
    async def fake_startup_sync(client, factory, settings):
        startup_sync_calls.append((client, factory, settings))
    monkeypatch.setattr("app.main.startup_sync", fake_startup_sync)

    # Stub ArqRedis pool so no real Redis connection is attempted.
    fake_redis = AsyncMock()
    fake_redis.aclose = AsyncMock()
    monkeypatch.setattr("app.main.create_pool", AsyncMock(return_value=fake_redis))

    # Run the lifespan
    from app.main import lifespan
    app = FastAPI()
    async with lifespan(app):
        # Assertions inside running lifespan
        assert len(provider_constructions) == 1
        assert len(startup_sync_calls) == 1
        assert hasattr(app.state, "task_provider")
        assert app.state.task_provider is fake_provider
    # After context exit, close was awaited
    assert app.state.task_provider.closed is True


@pytest.mark.asyncio
async def test_lifespan_skips_startup_sync_when_nirvana_active(monkeypatch, complete_env):
    """D-09: with TASK_PROVIDER=nirvana, startup_sync (Todoist Sync-API warm-up)
    is never called — Nirvana has no equivalent concept."""
    monkeypatch.setenv("TASK_PROVIDER", "nirvana")
    from app.core.config import get_settings
    get_settings.cache_clear()

    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    monkeypatch.setattr("app.main.create_async_engine", lambda *a, **k: fake_engine)

    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None
    fake_session_factory = MagicMock(return_value=fake_session)
    monkeypatch.setattr("app.main.async_sessionmaker", lambda *a, **k: fake_session_factory)

    monkeypatch.setattr("app.main.refresh_access_token", AsyncMock(
        return_value=("tok", datetime.now(timezone.utc))
    ))
    monkeypatch.setattr("app.main.load_token_from_kv", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr("app.main.upsert_kv", AsyncMock())

    meta_mock = AsyncMock(return_value={
        "todoist_task_id_api_name": "Todoist_Task_ID",
        "status_picklist_values": ["Completed"],
    })
    class FakeZohoClient:
        def __init__(self, access_token): pass
        async def get_fields_metadata(self, module="Tasks"):
            return await meta_mock(module)
    monkeypatch.setattr("app.main.ZohoClient", FakeZohoClient)

    async def fake_loop(ts, sf):
        await asyncio.sleep(3600)
    monkeypatch.setattr("app.main.proactive_refresh_loop", fake_loop)

    class FakeTaskProvider:
        def __init__(self):
            self.closed = False
        async def close(self):
            self.closed = True
    fake_provider = FakeTaskProvider()
    monkeypatch.setattr("app.main.get_provider", lambda settings: fake_provider)

    startup_sync_mock = AsyncMock()
    monkeypatch.setattr("app.main.startup_sync", startup_sync_mock)

    fake_redis = AsyncMock()
    fake_redis.aclose = AsyncMock()
    monkeypatch.setattr("app.main.create_pool", AsyncMock(return_value=fake_redis))

    from app.main import lifespan
    app = FastAPI()
    try:
        async with lifespan(app):
            startup_sync_mock.assert_not_called()
            assert app.state.task_provider is fake_provider
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_lifespan_startup_sync_failure_propagates(monkeypatch, complete_env):
    """If startup_sync raises, the lifespan startup fails loudly (SYNC-5)."""
    from app.core.config import get_settings
    get_settings.cache_clear()

    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    monkeypatch.setattr("app.main.create_async_engine", lambda *a, **k: fake_engine)

    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None
    fake_session_factory = MagicMock(return_value=fake_session)
    monkeypatch.setattr("app.main.async_sessionmaker", lambda *a, **k: fake_session_factory)

    monkeypatch.setattr("app.main.refresh_access_token", AsyncMock(
        return_value=("tok", datetime.now(timezone.utc))
    ))
    monkeypatch.setattr("app.main.load_token_from_kv", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr("app.main.upsert_kv", AsyncMock())

    meta_mock = AsyncMock(return_value={
        "todoist_task_id_api_name": "Todoist_Task_ID",
        "status_picklist_values": ["Completed"],
    })
    class FakeZohoClient:
        def __init__(self, access_token): pass
        async def get_fields_metadata(self, module="Tasks"):
            return await meta_mock(module)
    monkeypatch.setattr("app.main.ZohoClient", FakeZohoClient)

    async def fake_loop(ts, sf):
        await asyncio.sleep(3600)
    monkeypatch.setattr("app.main.proactive_refresh_loop", fake_loop)

    class FakeTaskProvider:
        async def close(self): pass
    monkeypatch.setattr("app.main.get_provider", lambda settings: FakeTaskProvider())

    from app.todoist.client import TodoistAuthError
    async def failing_startup_sync(*a, **kw):
        raise TodoistAuthError("bad token")
    monkeypatch.setattr("app.main.startup_sync", failing_startup_sync)

    from app.main import lifespan
    app = FastAPI()
    with pytest.raises(TodoistAuthError):
        async with lifespan(app):
            pass
