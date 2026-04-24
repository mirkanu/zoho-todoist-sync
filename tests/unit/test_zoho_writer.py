import json
import pytest
import resend
from app.zoho.writer import (
    update_zoho_task, complete_zoho_task,
    delete_zoho_task, write_todoist_id_to_zoho,
)
from app.zoho.client import (
    ZohoAuthError, ZohoNotFoundError, ZohoRateLimitError, ZohoAPIError,
)
from app.core.normalise import NormalisedTask

ZOHO_BASE = "https://www.zohoapis.eu/crm/v8"


# ---------------------------------------------------------------------------
# update_zoho_task tests
# ---------------------------------------------------------------------------

async def test_update_zoho_task_correct_payload(httpx_mock, complete_env):
    """SYNC-3: PUT /Tasks/{id} with correct Subject/Due_Date/Priority payload."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="PUT",
        status_code=200,
        json={"data": [{"status": "success", "code": "SUCCESS", "details": {}, "message": "record updated"}]},
    )
    normalised = NormalisedTask(title="New title", due_date="2026-06-01", priority=3, is_completed=False)
    await update_zoho_task("Z1", normalised, access_token="tok")

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"data": [{"Subject": "New title", "Due_Date": "2026-06-01", "Priority": "High"}]}
    assert req.headers["Authorization"] == "Zoho-oauthtoken tok"


async def test_update_zoho_task_clears_due_date_with_null(httpx_mock, complete_env):
    """EDGE-3 / A1: Due_Date=None must serialise to JSON null (key MUST be present)."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="PUT",
        status_code=200,
        json={"data": [{"status": "success", "code": "SUCCESS", "details": {}, "message": "record updated"}]},
    )
    normalised = NormalisedTask(title="T", due_date=None, priority=1, is_completed=False)
    await update_zoho_task("Z1", normalised, access_token="tok")

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body["data"][0]["Due_Date"] is None
    assert "Due_Date" in body["data"][0]  # key MUST be present, value null; NOT omitted
    assert body["data"][0]["Priority"] == "Low"


@pytest.mark.parametrize("todoist_int,zoho_str", [(4, "Highest"), (3, "High"), (2, "Normal"), (1, "Low")])
async def test_update_zoho_task_priority_roundtrip(httpx_mock, complete_env, todoist_int, zoho_str):
    """SYNC-2 reverse: each Todoist priority maps to the correct Zoho priority string."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="PUT",
        status_code=200,
        json={"data": [{"status": "success", "code": "SUCCESS", "details": {}, "message": "record updated"}]},
    )
    normalised = NormalisedTask(title="T", due_date=None, priority=todoist_int, is_completed=False)
    await update_zoho_task("Z1", normalised, access_token="tok")

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body["data"][0]["Priority"] == zoho_str


async def test_update_zoho_task_401_raises_auth_error(httpx_mock, complete_env):
    """401 response raises ZohoAuthError."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="PUT",
        status_code=401,
        json={},
    )
    normalised = NormalisedTask(title="T", due_date=None, priority=1, is_completed=False)
    with pytest.raises(ZohoAuthError):
        await update_zoho_task("Z1", normalised, access_token="tok")


async def test_update_zoho_task_404_raises_not_found(httpx_mock, complete_env):
    """404 response raises ZohoNotFoundError."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="PUT",
        status_code=404,
        json={},
    )
    normalised = NormalisedTask(title="T", due_date=None, priority=1, is_completed=False)
    with pytest.raises(ZohoNotFoundError):
        await update_zoho_task("Z1", normalised, access_token="tok")


async def test_update_zoho_task_429_raises_rate_limit(httpx_mock, complete_env):
    """429 response raises ZohoRateLimitError."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="PUT",
        status_code=429,
        json={},
    )
    normalised = NormalisedTask(title="T", due_date=None, priority=1, is_completed=False)
    with pytest.raises(ZohoRateLimitError):
        await update_zoho_task("Z1", normalised, access_token="tok")


async def test_update_zoho_task_207_with_error_raises(httpx_mock, complete_env):
    """Pitfall 2: 207 with per-record status 'error' raises ZohoAPIError."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="PUT",
        status_code=207,
        json={"data": [{"status": "error", "code": "INVALID_DATA", "message": "bad"}]},
    )
    normalised = NormalisedTask(title="T", due_date=None, priority=1, is_completed=False)
    with pytest.raises(ZohoAPIError, match="207"):
        await update_zoho_task("Z1", normalised, access_token="tok")


async def test_update_zoho_task_207_success_passes(httpx_mock, complete_env):
    """Pitfall 2: 207 with per-record status 'success' does NOT raise."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="PUT",
        status_code=207,
        json={"data": [{"status": "success", "code": "SUCCESS", "details": {}, "message": "record updated"}]},
    )
    normalised = NormalisedTask(title="T", due_date=None, priority=1, is_completed=False)
    # Must NOT raise
    await update_zoho_task("Z1", normalised, access_token="tok")


# ---------------------------------------------------------------------------
# complete_zoho_task tests
# ---------------------------------------------------------------------------

async def test_complete_zoho_task_uses_terminal_status(httpx_mock, complete_env, monkeypatch):
    """EDGE-4: complete_zoho_task uses first entry from ZOHO_TERMINAL_STATUSES, not hardcoded 'Completed'."""
    monkeypatch.setenv("ZOHO_TERMINAL_STATUSES", "Done,Cancelled")
    from app.core.config import get_settings
    get_settings.cache_clear()

    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z3",
        method="PUT",
        status_code=200,
        json={"data": [{"status": "success", "code": "SUCCESS", "details": {}, "message": "record updated"}]},
    )
    await complete_zoho_task("Z3", access_token="tok")

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body["data"][0]["Status"] == "Done"  # first entry, NOT "Completed"

    get_settings.cache_clear()  # restore default for subsequent tests


async def test_complete_zoho_task_default_completed(httpx_mock, complete_env):
    """EDGE-4 default: complete_env has ZOHO_TERMINAL_STATUSES='Completed' default."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z3",
        method="PUT",
        status_code=200,
        json={"data": [{"status": "success", "code": "SUCCESS", "details": {}, "message": "record updated"}]},
    )
    await complete_zoho_task("Z3", access_token="tok")

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body["data"][0]["Status"] == "Completed"


# ---------------------------------------------------------------------------
# delete_zoho_task tests
# ---------------------------------------------------------------------------

async def test_delete_zoho_task_sends_email(httpx_mock, complete_env, monkeypatch):
    """EDGE-2: Successful delete sends a Resend email notification."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="DELETE",
        status_code=200,
        json={"data": [{"status": "success", "code": "SUCCESS", "details": {}, "message": "record deleted"}]},
    )
    sent = []

    async def fake(p):
        sent.append(p)

    monkeypatch.setattr("resend.Emails.send_async", fake)
    monkeypatch.setattr(resend, "api_key", "re_test_key")

    await delete_zoho_task("Z1", access_token="tok")

    assert len(sent) == 1
    assert sent[0]["to"] == ["manuelkuhs@gmail.com"]
    assert "Z1" in sent[0]["subject"] or "Z1" in sent[0]["html"]


async def test_delete_zoho_task_404_idempotent_no_email(httpx_mock, complete_env, monkeypatch):
    """Pitfall 5: 404 on delete is idempotent — must NOT raise and must NOT send email."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="DELETE",
        status_code=404,
        json={},
    )
    sent = []

    async def fake(p):
        sent.append(p)

    monkeypatch.setattr("resend.Emails.send_async", fake)

    await delete_zoho_task("Z1", access_token="tok")  # must NOT raise

    assert len(sent) == 0


async def test_delete_zoho_task_resend_failure_does_not_raise(httpx_mock, complete_env, monkeypatch):
    """EDGE-6: Resend failure inside delete_zoho_task is caught/logged — must NOT re-raise."""
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="DELETE",
        status_code=200,
        json={"data": [{"status": "success", "code": "SUCCESS", "details": {}, "message": "record deleted"}]},
    )

    async def boom(p):
        raise RuntimeError("resend down")

    monkeypatch.setattr("resend.Emails.send_async", boom)

    await delete_zoho_task("Z1", access_token="tok")  # must NOT raise


# ---------------------------------------------------------------------------
# write_todoist_id_to_zoho tests
# ---------------------------------------------------------------------------

async def test_write_todoist_id_to_zoho_uses_cached_field_name(httpx_mock, complete_env):
    """SYNC-6 + Pitfall 3: write_todoist_id_to_zoho uses the field name from zoho_field_cache."""
    import app.zoho.state as state
    state.zoho_field_cache["todoist_task_id_api_name"] = "Todoist_Task_ID"

    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/Z1",
        method="PUT",
        status_code=200,
        json={"data": [{"status": "success", "code": "SUCCESS", "details": {}, "message": "ok"}]},
    )

    await write_todoist_id_to_zoho("Z1", "T999", access_token="tok")

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body["data"][0] == {"Todoist_Task_ID": "T999"}

    state.zoho_field_cache.clear()


async def test_write_todoist_id_to_zoho_raises_when_field_not_resolved(complete_env):
    """Pitfall 3: raises ZohoAPIError before any HTTP call when todoist_task_id_api_name is unresolved."""
    import app.zoho.state as state
    state.zoho_field_cache.clear()

    with pytest.raises(ZohoAPIError, match="not resolved"):
        await write_todoist_id_to_zoho("Z1", "T1", access_token="tok")
