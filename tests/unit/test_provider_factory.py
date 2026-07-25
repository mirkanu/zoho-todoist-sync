import pytest

from app.core.config import Settings
from app.providers.base import get_provider


def _settings(**overrides) -> Settings:
    defaults = dict(
        task_provider="todoist",
        todoist_api_token="test-todoist-token",
        nirvana_pat="test-nirvana-pat",
    )
    defaults.update(overrides)
    return Settings.model_construct(**defaults)


def test_get_provider_todoist_returns_todoist_client():
    settings = _settings(task_provider="todoist")
    provider = get_provider(settings)
    assert type(provider).__name__ == "TodoistClient"
    assert provider._api_token == "test-todoist-token"


def test_get_provider_nirvana_returns_nirvana_client():
    settings = _settings(task_provider="nirvana")
    provider = get_provider(settings)
    assert type(provider).__name__ == "NirvanaClient"
    assert provider._pat == "test-nirvana-pat"


def test_get_provider_unknown_raises_value_error():
    settings = _settings(task_provider="bogus")
    with pytest.raises(ValueError, match="bogus"):
        get_provider(settings)
