# tests/unit/test_migration_and_app.py
"""
Structural tests for the Alembic migration scaffold and FastAPI app stub.
Most tests are designed to run without a live database or real credentials.

The exceptions are `test_alembic_upgrade_head_renames_and_backfills_provider`
and `test_migration_002_backfills_existing_rows_to_todoist`, which exercise
the real 002_add_provider_column migration against a throwaway, disposable
Postgres container (never the production `zoho-sync-db` container) via the
`pg_test_db_url` fixture below. Those two tests skip themselves cleanly when
Docker isn't available in the current environment (e.g. restricted CI), so
the rest of this file's DB-free tests keep passing everywhere.
"""
import asyncio
import configparser
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid

import pytest


def test_alembic_ini_exists():
    assert os.path.isfile("alembic.ini"), "alembic.ini must exist at repo root"


def test_alembic_ini_has_alembic_section():
    c = configparser.ConfigParser()
    c.read("alembic.ini")
    assert "alembic" in c.sections(), "[alembic] section must exist in alembic.ini"


def test_alembic_ini_script_location():
    c = configparser.ConfigParser()
    c.read("alembic.ini")
    assert c["alembic"]["script_location"] == "app/db/migrations"


def test_migration_file_exists():
    assert os.path.isfile("app/db/migrations/versions/001_initial_schema.py")


def test_migration_revision_id():
    spec = importlib.util.spec_from_file_location(
        "mig", "app/db/migrations/versions/001_initial_schema.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "001_initial_schema"


def test_migration_down_revision_is_none():
    spec = importlib.util.spec_from_file_location(
        "mig2", "app/db/migrations/versions/001_initial_schema.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.down_revision is None


def test_migration_has_upgrade_callable():
    spec = importlib.util.spec_from_file_location(
        "mig3", "app/db/migrations/versions/001_initial_schema.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert callable(m.upgrade)


def test_migration_has_downgrade_callable():
    spec = importlib.util.spec_from_file_location(
        "mig4", "app/db/migrations/versions/001_initial_schema.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert callable(m.downgrade)


def test_migration_002_file_exists():
    assert os.path.isfile("app/db/migrations/versions/002_add_provider_column.py")


def test_migration_002_revision_id():
    spec = importlib.util.spec_from_file_location(
        "mig002_a", "app/db/migrations/versions/002_add_provider_column.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.revision == "002_add_provider_column"


def test_migration_002_down_revision_chains_from_001():
    spec = importlib.util.spec_from_file_location(
        "mig002_b", "app/db/migrations/versions/002_add_provider_column.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.down_revision == "001_initial_schema"


def test_migration_002_has_upgrade_and_downgrade_callables():
    spec = importlib.util.spec_from_file_location(
        "mig002_c", "app/db/migrations/versions/002_add_provider_column.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert callable(m.upgrade)
    assert callable(m.downgrade)


# ---------------------------------------------------------------------------
# Live-database tests for migration 002 (D-12) — use a throwaway Postgres
# container, never the production zoho-sync-db container.
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture()
def pg_test_db_url():
    """Spin up a disposable Postgres container solely for migration testing.

    Skips (does not fail) when Docker isn't available, so this stays green
    in restricted environments. Never touches the production zoho-sync-db
    container — a fresh, uniquely-named throwaway container is created and
    torn down within this fixture only.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker not available - cannot provision a throwaway test database")

    container_name = f"zoho-sync-migration-test-{uuid.uuid4().hex[:8]}"
    port = _find_free_port()

    try:
        subprocess.run(
            [
                "docker", "run", "-d", "--rm",
                "--name", container_name,
                "-e", "POSTGRES_DB=migration_test",
                "-e", "POSTGRES_USER=test",
                "-e", "POSTGRES_PASSWORD=test",
                "-p", f"{port}:5432",
                "postgres:16-alpine",
            ],
            check=True, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not start throwaway test database: {exc}")

    url = f"postgresql+asyncpg://test:test@localhost:{port}/migration_test"

    try:
        # Wait for Postgres to accept connections (container start is async).
        import asyncpg

        async def _wait_ready():
            last_exc = None
            for _ in range(30):
                try:
                    conn = await asyncpg.connect(
                        user="test", password="test", database="migration_test",
                        host="localhost", port=port,
                    )
                    await conn.close()
                    return
                except Exception as exc:  # noqa: BLE001 - retry until timeout
                    last_exc = exc
                    await asyncio.sleep(0.5)
            raise RuntimeError(f"test database never became ready: {last_exc}")

        asyncio.run(_wait_ready())
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


def _run_alembic_upgrade(db_url: str, revision: str = "head") -> None:
    """Run `alembic upgrade <revision>` against db_url in-process.

    env.py always reads app.core.config.settings.database_url (not the ini
    file), so we point the already-imported settings singleton at the
    throwaway test database before invoking the migration.
    """
    from alembic import command
    from alembic.config import Config

    import app.core.config as config_module
    config_module.settings.database_url = db_url

    cfg = Config("alembic.ini")
    command.upgrade(cfg, revision)


def test_alembic_upgrade_head_renames_and_backfills_provider(complete_env, pg_test_db_url):
    """Running `alembic upgrade head` from a fresh DB results in sync_state
    having external_task_id + provider columns, not todoist_task_id."""
    import asyncpg

    _run_alembic_upgrade(pg_test_db_url, "head")

    async def _inspect():
        conn = await asyncpg.connect(dsn=pg_test_db_url.replace("+asyncpg", ""))
        try:
            rows = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'sync_state'"
            )
            return {r["column_name"] for r in rows}
        finally:
            await conn.close()

    columns = asyncio.run(_inspect())
    assert "external_task_id" in columns
    assert "provider" in columns
    assert "todoist_task_id" not in columns


def test_migration_002_backfills_existing_rows_to_todoist(complete_env, pg_test_db_url):
    """A sync_state row inserted under 001's old todoist_task_id column
    before 002 runs ends up with provider='todoist' after upgrading to head
    (D-12: no production data loss on the provider backfill)."""
    import asyncpg

    # Step 1: upgrade to 001 only, then insert a row using the OLD column name,
    # simulating a pre-existing production row from before this migration.
    _run_alembic_upgrade(pg_test_db_url, "001_initial_schema")

    async def _insert_legacy_row():
        conn = await asyncpg.connect(dsn=pg_test_db_url.replace("+asyncpg", ""))
        try:
            await conn.execute(
                """
                INSERT INTO sync_state
                    (zoho_task_id, todoist_task_id, last_hash, last_synced_at)
                VALUES ($1, $2, $3, now())
                """,
                "ZOHO-EXISTING-1", "TD-EXISTING-1", "a" * 64,
            )
        finally:
            await conn.close()

    asyncio.run(_insert_legacy_row())

    # Step 2: run 002, which renames the column and backfills provider.
    _run_alembic_upgrade(pg_test_db_url, "002_add_provider_column")

    async def _read_row():
        conn = await asyncpg.connect(dsn=pg_test_db_url.replace("+asyncpg", ""))
        try:
            return await conn.fetchrow(
                "SELECT external_task_id, provider FROM sync_state WHERE zoho_task_id = $1",
                "ZOHO-EXISTING-1",
            )
        finally:
            await conn.close()

    row = asyncio.run(_read_row())
    assert row is not None
    assert row["external_task_id"] == "TD-EXISTING-1"
    assert row["provider"] == "todoist"


def test_env_py_exists():
    assert os.path.isfile("app/db/migrations/env.py")


def test_env_py_imports_base():
    with open("app/db/migrations/env.py") as f:
        content = f.read()
    assert "from app.db.models import Base" in content


def test_env_py_sets_target_metadata():
    with open("app/db/migrations/env.py") as f:
        content = f.read()
    assert "target_metadata = Base.metadata" in content


def test_env_py_uses_settings_database_url():
    with open("app/db/migrations/env.py") as f:
        content = f.read()
    assert "settings.database_url" in content


def test_env_py_uses_async_engine():
    with open("app/db/migrations/env.py") as f:
        content = f.read()
    assert "async_engine_from_config" in content


def test_main_py_exists():
    assert os.path.isfile("app/main.py")


def test_main_py_uses_lifespan():
    with open("app/main.py") as f:
        content = f.read()
    assert "@asynccontextmanager" in content
    assert "async def lifespan" in content
    assert "app = FastAPI(lifespan=lifespan)" in content


def test_main_py_logs_zoho_region():
    with open("app/main.py") as f:
        content = f.read()
    assert "zoho_region=settings.zoho_region" in content


def test_main_py_logs_todoist_task_id_field():
    with open("app/main.py") as f:
        content = f.read()
    assert "todoist_task_id_field=" in content


def test_main_py_no_deprecated_on_event():
    with open("app/main.py") as f:
        content = f.read()
    assert 'on_event("startup")' not in content


def test_main_py_importable_with_env():
    """Test that app/main.py can be imported when required env vars are present."""
    env_vars = {
        "ZOHO_CLIENT_ID": "x",
        "ZOHO_CLIENT_SECRET": "x",
        "ZOHO_REFRESH_TOKEN": "x",
        "ZOHO_USER_ID": "x",
        "TODOIST_API_TOKEN": "x",
        "TODOIST_PROJECT_ID": "x",
        "TODOIST_CLIENT_SECRET": "x",
        "RESEND_API_KEY": "x",
        "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    for k, v in env_vars.items():
        os.environ[k] = v

    # Snapshot existing app modules so we can restore them after this test.
    # Without restoration, subsequent tests that monkeypatch module-level names
    # (e.g. app.todoist.sync_manager.upsert_kv) receive a stale module object
    # while the running code uses the freshly-imported one — causing ghost failures.
    saved_app_modules = {k: v for k, v in sys.modules.items() if k.startswith("app")}

    # Remove cached modules to allow fresh import
    for mod in list(sys.modules.keys()):
        if mod.startswith("app"):
            del sys.modules[mod]

    try:
        from app.main import app as fastapi_app
        assert fastapi_app is not None
    finally:
        # Restore previous app module objects so subsequent tests see a
        # consistent module namespace (their monkeypatches target the right object).
        for mod in list(sys.modules.keys()):
            if mod.startswith("app"):
                del sys.modules[mod]
        sys.modules.update(saved_app_modules)
