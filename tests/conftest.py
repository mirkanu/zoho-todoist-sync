# tests/conftest.py
import os
import pytest

REQUIRED_ENV = {
    "ZOHO_CLIENT_ID": "test-client-id",
    "ZOHO_CLIENT_SECRET": "test-client-secret",
    "ZOHO_REFRESH_TOKEN": "test-refresh-token",
    "ZOHO_USER_ID": "test-user-id",
    "ZOHO_ORG_ID": "test-org",
    "TODOIST_API_TOKEN": "test-todoist-token",
    "TODOIST_PROJECT_ID": "6gCPcWwM392GhXQh",
    "TODOIST_CLIENT_SECRET": "test-todoist-client-secret",
    "NIRVANA_PAT": "test-nirvana-pat",
    "RESEND_API_KEY": "test-resend-key",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "REDIS_URL": "redis://localhost:6379/0",
}

@pytest.fixture
def complete_env(monkeypatch):
    """Populate every required env var with a dummy value."""
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    return REQUIRED_ENV
