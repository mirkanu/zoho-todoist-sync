# tests/unit/test_zoho_client.py
import pytest
from app.zoho.client import (
    ZohoClient,
    ZohoAuthError,
    ZohoNotFoundError,
    ZohoRateLimitError,
    ZohoAPIError,
)

ZOHO_BASE = "https://www.zohoapis.eu/crm/v8"


async def test_get_task_success(httpx_mock):
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/123",
        json={"data": [{"id": "123", "Subject": "Buy milk"}]},
    )
    client = ZohoClient(access_token="test-token")
    result = await client.get_task("123")
    assert result["data"][0]["Subject"] == "Buy milk"

async def test_get_task_401_raises_auth_error(httpx_mock):
    httpx_mock.add_response(url=f"{ZOHO_BASE}/Tasks/123", status_code=401)
    client = ZohoClient(access_token="bad-token")
    with pytest.raises(ZohoAuthError):
        await client.get_task("123")

async def test_get_task_404_raises_not_found(httpx_mock):
    httpx_mock.add_response(url=f"{ZOHO_BASE}/Tasks/999", status_code=404)
    client = ZohoClient(access_token="test-token")
    with pytest.raises(ZohoNotFoundError):
        await client.get_task("999")

async def test_get_task_429_raises_rate_limit(httpx_mock):
    httpx_mock.add_response(url=f"{ZOHO_BASE}/Tasks/123", status_code=429)
    client = ZohoClient(access_token="test-token")
    with pytest.raises(ZohoRateLimitError):
        await client.get_task("123")

async def test_get_task_500_raises_api_error(httpx_mock):
    httpx_mock.add_response(url=f"{ZOHO_BASE}/Tasks/123", status_code=500)
    client = ZohoClient(access_token="test-token")
    with pytest.raises(ZohoAPIError):
        await client.get_task("123")

async def test_get_fields_metadata_resolves_todoist_field(httpx_mock):
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/settings/fields?module=Tasks",
        json={"fields": [
            {"api_name": "Todoist_Task_ID", "field_label": "Todoist Task ID",
             "custom_field": True, "data_type": "text"},
            {"api_name": "Status", "data_type": "picklist",
             "pick_list_values": [{"actual_value": "Not Started", "display_value": "Not Started"},
                                   {"actual_value": "Completed", "display_value": "Completed"}]},
        ]},
    )
    client = ZohoClient(access_token="test-token")
    meta = await client.get_fields_metadata("Tasks")
    assert meta["todoist_task_id_api_name"] == "Todoist_Task_ID"
    assert "Completed" in meta["status_picklist_values"]
    assert "Not Started" in meta["status_picklist_values"]

async def test_get_fields_metadata_returns_none_when_no_todoist_field(httpx_mock):
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/settings/fields?module=Tasks",
        json={"fields": [{"api_name": "Subject", "field_label": "Subject",
                          "custom_field": False, "data_type": "text"}]},
    )
    client = ZohoClient(access_token="test-token")
    meta = await client.get_fields_metadata("Tasks")
    assert meta["todoist_task_id_api_name"] is None
    assert meta["status_picklist_values"] == []

async def test_fetch_modified_since_paginates(httpx_mock):
    # Page 1: more_records=True
    httpx_mock.add_response(
        url__startswith=f"{ZOHO_BASE}/Tasks/search",
        json={"data": [{"id": "1"}, {"id": "2"}],
              "info": {"per_page": 200, "count": 2, "page": 1, "more_records": True}},
    )
    # Page 2: more_records=False
    httpx_mock.add_response(
        url__startswith=f"{ZOHO_BASE}/Tasks/search",
        json={"data": [{"id": "3"}],
              "info": {"per_page": 200, "count": 1, "page": 2, "more_records": False}},
    )
    from datetime import datetime, timezone
    client = ZohoClient(access_token="test-token")
    results = await client.fetch_tasks_modified_since(
        since=datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc),
        owner_id="554023000000235011",
    )
    assert len(results) == 3
    assert [r["id"] for r in results] == ["1", "2", "3"]

async def test_fetch_modified_since_204_is_empty_list(httpx_mock):
    httpx_mock.add_response(url__startswith=f"{ZOHO_BASE}/Tasks/search", status_code=204)
    from datetime import datetime, timezone
    client = ZohoClient(access_token="test-token")
    results = await client.fetch_tasks_modified_since(
        since=datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc),
        owner_id="554023000000235011",
    )
    assert results == []

async def test_fetch_modified_since_criteria_has_modified_time_and_owner(httpx_mock):
    httpx_mock.add_response(
        url__startswith=f"{ZOHO_BASE}/Tasks/search",
        json={"data": [], "info": {"more_records": False, "per_page": 200, "count": 0, "page": 1}},
    )
    from datetime import datetime, timezone
    client = ZohoClient(access_token="test-token")
    await client.fetch_tasks_modified_since(
        since=datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc),
        owner_id="554023000000235011",
    )
    req = httpx_mock.get_requests()[0]
    criteria = req.url.params.get("criteria")
    assert criteria is not None
    assert "Modified_Time:greater_equal:2026-04-23T10:00:00+00:00" in criteria
    assert "Owner:equals:554023000000235011" in criteria

async def test_get_task_sends_oauth_header(httpx_mock):
    httpx_mock.add_response(
        url=f"{ZOHO_BASE}/Tasks/123",
        json={"data": [{"id": "123"}]},
    )
    client = ZohoClient(access_token="abc123")
    await client.get_task("123")
    req = httpx_mock.get_requests()[0]
    assert req.headers["Authorization"] == "Zoho-oauthtoken abc123"
