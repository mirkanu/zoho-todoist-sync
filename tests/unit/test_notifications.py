"""Unit tests for app.core.notifications — sender externalisation (NOTIF-1, NOTIF-2)."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
def _clear_settings_cache():
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_default_sender_is_resend_dev_placeholder(complete_env, _clear_settings_cache):
    from app.core.notifications import send_deletion_notification
    with patch("app.core.notifications.resend.Emails.send_async", new_callable=AsyncMock) as mock_send:
        await send_deletion_notification("subj", "<p>body</p>")
    mock_send.assert_awaited_once()
    sent_params = mock_send.await_args.args[0]
    assert sent_params["from"] == "sync-alerts@resend.dev"


@pytest.mark.asyncio
async def test_sender_overridden_by_env_var(complete_env, monkeypatch, _clear_settings_cache):
    monkeypatch.setenv("RESEND_SENDER_EMAIL", "sync-alerts@mail.gsdlabs.dev")
    from app.core.config import get_settings
    get_settings.cache_clear()  # force re-read of env after setenv
    from app.core.notifications import send_deletion_notification
    with patch("app.core.notifications.resend.Emails.send_async", new_callable=AsyncMock) as mock_send:
        await send_deletion_notification("subj", "<p>body</p>")
    sent_params = mock_send.await_args.args[0]
    assert sent_params["from"] == "sync-alerts@mail.gsdlabs.dev"


@pytest.mark.asyncio
async def test_send_failure_does_not_raise(complete_env, _clear_settings_cache):
    """EDGE-6: Resend exceptions must be swallowed."""
    from app.core.notifications import send_deletion_notification
    with patch(
        "app.core.notifications.resend.Emails.send_async",
        new_callable=AsyncMock,
        side_effect=RuntimeError("resend down"),
    ):
        # Must not raise
        await send_deletion_notification("subj", "<p>body</p>")
