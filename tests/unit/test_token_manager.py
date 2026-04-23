# tests/unit/test_token_manager.py
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.zoho.token_manager import (
    ACCOUNTS_URL_EU,
    KV_ACCESS_TOKEN_KEY,
    KV_EXPIRES_AT_KEY,
    REFRESH_INTERVAL_SECS,
    proactive_refresh_loop,
    refresh_access_token,
)


def test_REFRESH_INTERVAL_SECS_is_50_minutes():
    assert REFRESH_INTERVAL_SECS == 3000

def test_accounts_url_is_eu():
    assert ACCOUNTS_URL_EU == "https://accounts.zoho.eu/oauth/v2/token"


async def test_refresh_access_token_success(httpx_mock, complete_env):
    httpx_mock.add_response(
        url=ACCOUNTS_URL_EU,
        method="POST",
        json={"access_token": "1000.new_token_value", "token_type": "Bearer",
              "expires_in": 3600, "api_domain": "https://www.zohoapis.eu"},
    )
    from app.core.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    token, expires_at = await refresh_access_token(settings)
    assert token == "1000.new_token_value"
    assert isinstance(expires_at, datetime)
    assert expires_at.tzinfo is not None   # timezone-aware
    assert expires_at > datetime.now(timezone.utc) + timedelta(seconds=3500)


async def test_refresh_access_token_raises_on_error_body(httpx_mock, complete_env):
    httpx_mock.add_response(
        url=ACCOUNTS_URL_EU, method="POST",
        json={"error": "invalid_code"},
    )
    from app.core.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    with pytest.raises(RuntimeError, match="invalid_code"):
        await refresh_access_token(settings)


async def test_refresh_access_token_raises_on_non_200(httpx_mock, complete_env):
    httpx_mock.add_response(url=ACCOUNTS_URL_EU, method="POST", status_code=500, text="oops")
    from app.core.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    with pytest.raises(RuntimeError, match="500"):
        await refresh_access_token(settings)


async def test_refresh_access_token_posts_correct_form_body(httpx_mock, complete_env):
    httpx_mock.add_response(
        url=ACCOUNTS_URL_EU, method="POST",
        json={"access_token": "t", "expires_in": 3600},
    )
    from app.core.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    await refresh_access_token(settings)
    req = httpx_mock.get_requests()[0]
    body = req.read().decode()
    assert "grant_type=refresh_token" in body
    assert "client_id=test-client-id" in body
    assert "client_secret=test-client-secret" in body
    assert "refresh_token=test-refresh-token" in body


async def test_refresh_access_token_does_not_log_token_value(httpx_mock, complete_env, caplog):
    secret_token = "1000.SECRET_TOKEN_SHOULD_NEVER_LOG"
    httpx_mock.add_response(
        url=ACCOUNTS_URL_EU, method="POST",
        json={"access_token": secret_token, "expires_in": 3600},
    )
    from app.core.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    with caplog.at_level(logging.INFO):
        await refresh_access_token(settings)
    # The token must NOT appear anywhere in any log record.
    for rec in caplog.records:
        assert secret_token not in rec.getMessage()
        assert secret_token not in str(getattr(rec, "args", ""))


async def test_proactive_refresh_loop_updates_token_state(monkeypatch, complete_env):
    # Stub asyncio.sleep so the loop's 3000s wait returns immediately, and stop after 1 iteration.
    import asyncio as _asyncio
    sleep_calls = {"n": 0}
    async def fake_sleep(secs):
        sleep_calls["n"] += 1
        if sleep_calls["n"] > 1:
            raise RuntimeError("stop-loop-after-one-iteration")
    monkeypatch.setattr("app.zoho.token_manager.asyncio.sleep", fake_sleep)

    async def fake_refresh(settings):
        return "new_tok", datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("app.zoho.token_manager.refresh_access_token", fake_refresh)

    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = None
    session_factory = MagicMock(return_value=fake_session)

    async def fake_upsert(session, key, value):
        return None
    monkeypatch.setattr("app.zoho.token_manager.upsert_kv", fake_upsert)

    token_state: dict = {}
    with pytest.raises(RuntimeError, match="stop-loop-after-one-iteration"):
        await proactive_refresh_loop(token_state, session_factory)
    assert token_state["access_token"] == "new_tok"
    assert token_state["expires_at"] == datetime(2030, 1, 1, tzinfo=timezone.utc)


async def test_proactive_refresh_loop_reraises_on_failure(monkeypatch, complete_env):
    async def fake_sleep(secs): return None
    monkeypatch.setattr("app.zoho.token_manager.asyncio.sleep", fake_sleep)

    async def fake_refresh(settings):
        raise RuntimeError("network down")
    monkeypatch.setattr("app.zoho.token_manager.refresh_access_token", fake_refresh)

    session_factory = MagicMock()
    token_state: dict = {}
    with pytest.raises(RuntimeError, match="network down"):
        await proactive_refresh_loop(token_state, session_factory)
