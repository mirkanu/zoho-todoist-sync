# app/zoho/token_manager.py
# OAuth refresh-token lifecycle for Zoho CRM EU.
# - refresh_access_token(settings): POST to accounts.zoho.eu, return (token, expires_at)
# - upsert_kv(session, key, value): idempotent insert-or-update on kv_store
# - proactive_refresh_loop(...): asyncio.Task that refreshes every 50 min; re-raises on failure
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import KVStore

if TYPE_CHECKING:
    from app.core.config import Settings

log = get_logger(__name__)

# 50 minutes — 10-minute safety margin on the 60-minute Zoho token lifetime.
REFRESH_INTERVAL_SECS: int = 50 * 60  # = 3000

ACCOUNTS_URL_EU: str = "https://accounts.zoho.eu/oauth/v2/token"

KV_ACCESS_TOKEN_KEY: str = "zoho_access_token"
KV_EXPIRES_AT_KEY: str = "zoho_token_expires_at"


async def refresh_access_token(settings: Settings) -> tuple[str, datetime]:
    """
    Exchange the refresh token for a new access token at accounts.zoho.eu.
    Returns (access_token, expires_at). Raises RuntimeError on any failure —
    caller MUST NOT silently retry (INFRA-6: refresh failure stops the service).
    Never logs the access_token value — only expiry time.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            ACCOUNTS_URL_EU,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.zoho_client_id,
                "client_secret": settings.zoho_client_secret,
                "refresh_token": settings.zoho_refresh_token,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Zoho token refresh failed: HTTP {resp.status_code} — {resp.text[:200]}"
        )
    body = resp.json()
    if "access_token" not in body:
        # Zoho returns HTTP 200 with {"error": "invalid_code"} on bad refresh token.
        raise RuntimeError(
            f"Zoho token refresh response missing access_token: error={body.get('error', 'unknown')}"
        )
    expires_in = int(body.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    log.info("zoho_token_refreshed", expires_at=expires_at.isoformat(), expires_in=expires_in)
    return body["access_token"], expires_at


async def upsert_kv(session: AsyncSession, key: str, value: str) -> None:
    """Idempotent upsert of a kv_store row. DB trigger updates updated_at."""
    existing = await session.get(KVStore, key)
    if existing is None:
        session.add(KVStore(key=key, value=value))
    else:
        existing.value = value
    await session.commit()


async def load_token_from_kv(session: AsyncSession) -> tuple[str | None, datetime | None]:
    """Load previously-persisted token + expiry from kv_store. None if absent."""
    token_row = await session.get(KVStore, KV_ACCESS_TOKEN_KEY)
    expires_row = await session.get(KVStore, KV_EXPIRES_AT_KEY)
    token = token_row.value if token_row else None
    expires_at: datetime | None = None
    if expires_row and expires_row.value:
        try:
            expires_at = datetime.fromisoformat(expires_row.value)
        except ValueError:
            expires_at = None
    return token, expires_at


async def proactive_refresh_loop(
    token_state: dict,
    session_factory: Callable[[], AsyncSession],
) -> None:
    """
    asyncio.Task body: sleep REFRESH_INTERVAL_SECS, refresh, persist, repeat.

    On any refresh failure: log ERROR and RE-RAISE (INFRA-6). The exception
    kills this task; a supervisor or the next request's auth error will
    surface the problem. Never silent retry.
    """
    import app.zoho.token_manager as _self
    from app.core.config import get_settings
    settings = get_settings()
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECS)
        try:
            new_token, new_expires_at = await _self.refresh_access_token(settings)
            token_state["access_token"] = new_token
            token_state["expires_at"] = new_expires_at
            async with session_factory() as session:
                await _self.upsert_kv(session, KV_ACCESS_TOKEN_KEY, new_token)
                await _self.upsert_kv(session, KV_EXPIRES_AT_KEY, new_expires_at.isoformat())
        except Exception as exc:
            log.error("zoho_token_refresh_failed", error=str(exc))
            raise
