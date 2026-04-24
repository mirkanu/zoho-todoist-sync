# tests/unit/test_migration_and_app.py
"""
Structural tests for the Alembic migration scaffold and FastAPI app stub.
All tests are designed to run without a live database or real credentials.
"""
import configparser
import importlib.util
import os
import sys


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
