"""Unit tests for WorkerSettings, on_startup (with refresh task), on_shutdown (with cancel), and enqueue_sync.

Requirements: INFRA-1, INFRA-3, SYNC-10, LOOP-4
"""
import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_settings_cache(complete_env):
    """Clear settings cache before and after each test; depends on complete_env so env vars are set first."""
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 1: redis_settings is a RedisSettings instance built from REDIS_URL
# ---------------------------------------------------------------------------

def test_redis_settings_from_dsn(complete_env):
    """INFRA-3: WorkerSettings.redis_settings is built from env var REDIS_URL."""
    import importlib
    from arq.connections import RedisSettings
    import app.worker.settings as ws_mod
    importlib.reload(ws_mod)
    rs = ws_mod.WorkerSettings.redis_settings
    assert isinstance(rs, RedisSettings)
    assert rs.host == "localhost"
    assert rs.port == 6379


# ---------------------------------------------------------------------------
# Test 2: functions list registers sync_task with correct config
# ---------------------------------------------------------------------------

def test_sync_task_registered_with_correct_func_config(complete_env):
    """WorkerSettings.functions wraps sync_task with timeout=60, keep_result=300, max_tries=4.

    arq's Function dataclass stores these as timeout_s and keep_result_s (in seconds).
    """
    import importlib
    import app.worker.settings as ws_mod
    importlib.reload(ws_mod)
    functions = ws_mod.WorkerSettings.functions
    assert len(functions) == 1
    fn = functions[0]
    # arq Function stores timeout as timeout_s and keep_result as keep_result_s
    assert fn.timeout_s == 60
    assert fn.keep_result_s == 300
    assert fn.max_tries == 4


# ---------------------------------------------------------------------------
# Test 3: on_startup and on_shutdown are callable
# ---------------------------------------------------------------------------

def test_worker_settings_has_on_startup_and_on_shutdown(complete_env):
    import importlib
    import app.worker.settings as ws_mod
    importlib.reload(ws_mod)
    assert callable(ws_mod.WorkerSettings.on_startup)
    assert callable(ws_mod.WorkerSettings.on_shutdown)


# ---------------------------------------------------------------------------
# Test 4: max_jobs default is 10
# ---------------------------------------------------------------------------

def test_worker_settings_max_jobs_default(complete_env):
    import importlib
    import app.worker.settings as ws_mod
    importlib.reload(ws_mod)
    assert ws_mod.WorkerSettings.max_jobs == 10


# ---------------------------------------------------------------------------
# Test 5: on_startup populates ctx (INFRA-1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_startup_populates_ctx(complete_env):
    """INFRA-1: on_startup must populate ctx with session_factory, engine, zoho_client, todoist_client, _refresh_task."""
    import importlib
    import app.worker.settings as ws_mod
    importlib.reload(ws_mod)
    from app.worker.settings import on_startup

    fake_engine = MagicMock()
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)
    fake_session_factory = MagicMock(return_value=fake_session)

    refresh_task_mock = MagicMock()
    future_dt = datetime.now(timezone.utc) + timedelta(hours=1)

    async def fake_refresh_loop_coro(ts, sf):
        pass

    with patch("app.worker.settings.create_async_engine", return_value=fake_engine), \
         patch("app.worker.settings.async_sessionmaker", return_value=fake_session_factory), \
         patch("app.worker.settings.load_token_from_kv", new_callable=AsyncMock, return_value=(None, None)), \
         patch("app.worker.settings.refresh_access_token", new_callable=AsyncMock, return_value=("new_tok", future_dt)), \
         patch("app.worker.settings.upsert_kv", new_callable=AsyncMock), \
         patch("app.worker.settings.ZohoClient"), \
         patch("app.worker.settings.TodoistClient"), \
         patch("app.worker.settings.resend"), \
         patch("app.worker.settings.proactive_refresh_loop", return_value=fake_refresh_loop_coro(None, None)), \
         patch("app.worker.settings.asyncio.create_task", return_value=refresh_task_mock) as mock_create_task:

        ctx = {}
        await on_startup(ctx)

    assert "session_factory" in ctx
    assert "engine" in ctx
    assert "zoho_client" in ctx
    assert "todoist_client" in ctx
    assert ctx["_refresh_task"] is refresh_task_mock
    mock_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# Test 6: on_startup launches proactive_refresh_loop (Pitfall 7 / LOOP-Zoho-token)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_startup_launches_proactive_refresh_loop(complete_env):
    """Pitfall 7: proactive_refresh_loop must be started as an asyncio task in on_startup."""
    import importlib
    import app.worker.settings as ws_mod
    importlib.reload(ws_mod)
    from app.worker.settings import on_startup

    fake_engine = MagicMock()
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)
    fake_session_factory = MagicMock(return_value=fake_session)

    refresh_task_mock = MagicMock()
    future_dt = datetime.now(timezone.utc) + timedelta(hours=1)

    async def fake_refresh_loop(ts, sf):
        pass

    # Sentinel coroutine returned by proactive_refresh_loop call
    sentinel_coro = fake_refresh_loop(None, None)
    mock_loop = MagicMock(return_value=sentinel_coro)

    with patch("app.worker.settings.create_async_engine", return_value=fake_engine), \
         patch("app.worker.settings.async_sessionmaker", return_value=fake_session_factory), \
         patch("app.worker.settings.load_token_from_kv", new_callable=AsyncMock, return_value=(None, None)), \
         patch("app.worker.settings.refresh_access_token", new_callable=AsyncMock, return_value=("new_tok", future_dt)), \
         patch("app.worker.settings.upsert_kv", new_callable=AsyncMock), \
         patch("app.worker.settings.ZohoClient"), \
         patch("app.worker.settings.TodoistClient"), \
         patch("app.worker.settings.resend"), \
         patch("app.worker.settings.proactive_refresh_loop", mock_loop), \
         patch("app.worker.settings.asyncio.create_task", return_value=refresh_task_mock) as mock_create_task:

        ctx = {}
        await on_startup(ctx)

    # proactive_refresh_loop was called exactly once
    mock_loop.assert_called_once()

    # The return value of proactive_refresh_loop(...) was passed to asyncio.create_task
    mock_create_task.assert_called_once_with(sentinel_coro)


# ---------------------------------------------------------------------------
# Test 7: on_shutdown cancels refresh task and closes clients
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_shutdown_cancels_refresh_task_and_closes_clients(complete_env):
    """on_shutdown must cancel _refresh_task, await it (swallowing CancelledError), close todoist_client, dispose engine."""
    import importlib
    import app.worker.settings as ws_mod
    importlib.reload(ws_mod)
    from app.worker.settings import on_shutdown

    cancel_called = []

    class FakeTask:
        def cancel(self):
            cancel_called.append(True)

        def __await__(self):
            raise asyncio.CancelledError()
            yield  # make it a generator (unreachable but required for syntax)

    refresh_task = FakeTask()
    todoist_client = AsyncMock()
    engine = MagicMock()
    engine.dispose = AsyncMock()

    ctx = {
        "_refresh_task": refresh_task,
        "todoist_client": todoist_client,
        "engine": engine,
    }

    await on_shutdown(ctx)

    assert cancel_called, "refresh_task.cancel() was not called"
    todoist_client.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 8: on_shutdown tolerates missing refresh task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_shutdown_tolerates_missing_refresh_task(complete_env):
    """on_shutdown must not raise if _refresh_task is absent from ctx."""
    import importlib
    import app.worker.settings as ws_mod
    importlib.reload(ws_mod)
    from app.worker.settings import on_shutdown

    todoist_client = AsyncMock()
    engine = MagicMock()
    engine.dispose = AsyncMock()

    ctx = {
        "todoist_client": todoist_client,
        "engine": engine,
        # no _refresh_task
    }

    # Must not raise
    await on_shutdown(ctx)

    todoist_client.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 9: enqueue_sync deduplication (SYNC-10)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_sync_dedups(complete_env):
    """SYNC-10: When enqueue_job returns None (dedup), enqueue_sync returns without raising."""
    from app.worker.enqueue import enqueue_sync

    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock(return_value=None)

    result = await enqueue_sync(mock_redis, "Z1", defer_secs=0)

    mock_redis.enqueue_job.assert_awaited_once_with(
        "sync_task",
        "Z1",
        _job_id="sync:Z1",
        _defer_by=0,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Test 10: enqueue_sync passes defer_secs as _defer_by (LOOP-4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_sync_defers_by_zoho_secs(complete_env):
    """LOOP-4: enqueue_sync must forward defer_secs as _defer_by to arq."""
    from app.worker.enqueue import enqueue_sync

    mock_job = MagicMock()
    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock(return_value=mock_job)

    await enqueue_sync(mock_redis, "Z1", defer_secs=2)

    call_kwargs = mock_redis.enqueue_job.call_args
    assert call_kwargs.kwargs["_defer_by"] == 2
    assert call_kwargs.kwargs["_job_id"] == "sync:Z1"


# ---------------------------------------------------------------------------
# Test 11: enqueue_sync default defer is zero
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_sync_default_defer_is_zero(complete_env):
    """When enqueue_sync is called without defer_secs, _defer_by must default to 0."""
    from app.worker.enqueue import enqueue_sync

    mock_job = MagicMock()
    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock(return_value=mock_job)

    await enqueue_sync(mock_redis, "Z2")

    call_kwargs = mock_redis.enqueue_job.call_args
    assert call_kwargs.kwargs["_defer_by"] == 0


# ---------------------------------------------------------------------------
# Test 12: __main__ calls run_worker(WorkerSettings) (INFRA-1)
# ---------------------------------------------------------------------------

def test_main_module_calls_run_worker(complete_env):
    """Verify app/worker/__main__.py contains the correct entry-point boilerplate."""
    src = (ROOT / "app/worker/__main__.py").read_text()
    assert "from arq import run_worker" in src
    assert "run_worker(WorkerSettings)" in src
    assert 'if __name__ == "__main__"' in src
