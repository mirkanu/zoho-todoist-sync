# tests/unit/test_config.py
import os
import subprocess
import sys
import pytest


def test_settings_raises_on_missing_required_var(tmp_path, monkeypatch):
    """INFRA-5: module-level `settings = Settings()` must raise when ZOHO_CLIENT_ID is missing."""
    # Run in a clean subprocess so import caching in the current process does not interfere.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ZOHO_", "TODOIST_", "RESEND_", "DATABASE_", "REDIS_"))}
    # Ensure .env in CWD is not picked up
    result = subprocess.run(
        [sys.executable, "-c", "from app.core.config import settings"],
        env={**env, "PYTHONPATH": os.getcwd()},
        cwd=str(tmp_path),  # cwd with no .env file
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ValidationError" in result.stderr or "validation error" in result.stderr.lower()


def test_settings_succeeds_with_complete_env(complete_env):
    """INFRA-5 happy path: Settings() constructs when all required vars are set."""
    # Force a fresh import so it picks up monkeypatched env.
    import importlib
    import app.core.config as config_mod
    importlib.reload(config_mod)
    assert config_mod.settings.zoho_client_id == "test-client-id"
    assert config_mod.settings.todoist_project_id == "6gCPcWwM392GhXQh"


def test_zoho_region_default(complete_env):
    from app.core.config import Settings
    s = Settings()
    assert s.zoho_region == "eu"


def test_zoho_todoist_task_id_field_default(complete_env):
    from app.core.config import Settings
    s = Settings()
    assert s.zoho_todoist_task_id_field == ""


def test_zoho_job_defer_secs_default(complete_env):
    from app.core.config import Settings
    s = Settings()
    assert s.zoho_job_defer_secs == 2


def test_log_level_default(complete_env):
    from app.core.config import Settings
    s = Settings()
    assert s.log_level == "INFO"


def test_nirvana_pat_from_complete_env(complete_env):
    from app.core.config import Settings
    s = Settings()
    assert s.nirvana_pat == "test-nirvana-pat"


def test_nirvana_pat_missing_raises(complete_env, monkeypatch):
    from app.core.config import Settings
    monkeypatch.delenv("NIRVANA_PAT", raising=False)
    with pytest.raises(Exception):
        Settings()


def test_task_provider_default(complete_env):
    from app.core.config import Settings
    s = Settings()
    assert s.task_provider == "todoist"


def test_terminal_statuses_list_single():
    from app.core.config import Settings
    s = Settings.model_construct(zoho_terminal_statuses="Completed")
    assert s.zoho_terminal_statuses_list == ["Completed"]


def test_terminal_statuses_list_multiple():
    from app.core.config import Settings
    s = Settings.model_construct(zoho_terminal_statuses="Completed,Closed,Done")
    assert s.zoho_terminal_statuses_list == ["Completed", "Closed", "Done"]


def test_terminal_statuses_list_with_spaces():
    from app.core.config import Settings
    s = Settings.model_construct(zoho_terminal_statuses="Completed, Closed , Done")
    assert s.zoho_terminal_statuses_list == ["Completed", "Closed", "Done"]
