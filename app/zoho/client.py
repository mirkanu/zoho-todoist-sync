# app/zoho/client.py
import httpx
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

ZOHO_EU_BASE_URL = "https://www.zohoapis.eu/crm/v8"


class ZohoAuthError(Exception):
    """Raised on 401 — refresh token invalid or scope mismatch. Do NOT retry — stop and alert."""


class ZohoNotFoundError(Exception):
    """Raised on 404 — task or resource does not exist."""


class ZohoRateLimitError(Exception):
    """Raised on 429 — Zoho concurrency limit exceeded. Retry with backoff."""


class ZohoAPIError(Exception):
    """Raised on other non-2xx responses (5xx, unexpected 4xx)."""


@dataclass
class ZohoClient:
    """
    Async Zoho CRM v8 client against the EU region.
    access_token is mutable — refreshed in-place by token_manager.proactive_refresh_loop.
    """
    access_token: str
    base_url: str = ZOHO_EU_BASE_URL

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Zoho-oauthtoken {self.access_token}"}

    def _handle(self, resp: httpx.Response, context: str = "") -> Any:
        """Map Zoho non-2xx responses to typed exceptions. Returns parsed JSON on 2xx."""
        if resp.status_code == 401:
            raise ZohoAuthError(f"401 Unauthorized — {context}")
        if resp.status_code in (204, 404):
            raise ZohoNotFoundError(f"{resp.status_code} Not Found — {context}")
        if resp.status_code == 429:
            raise ZohoRateLimitError(f"429 Rate limit — {context}")
        if not (200 <= resp.status_code < 300):
            raise ZohoAPIError(f"{resp.status_code} — {context}: {resp.text[:200]}")
        return resp.json()

    async def get_task(self, zoho_task_id: str) -> dict[str, Any]:
        """
        Fetch a single Zoho Tasks record by ID.
        Returns the full response dict: {"data": [{...record...}]}.
        Raises ZohoAuthError/ZohoNotFoundError/ZohoRateLimitError/ZohoAPIError.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/Tasks/{zoho_task_id}",
                headers=self._headers(),
            )
        return self._handle(resp, f"GET /Tasks/{zoho_task_id}")

    async def get_fields_metadata(self, module: str = "Tasks") -> dict:
        """
        Fetch field metadata for a Zoho module and resolve:
          - todoist_task_id_api_name: the api_name of the custom field whose field_label contains "Todoist"
            (or None if not found — caller should log WARN and continue)
          - status_picklist_values: the list of actual_value strings from the Status picklist
            (or [] if Status is not a picklist field)
        Required OAuth scope: ZohoCRM.settings.fields.ALL
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/settings/fields",
                params={"module": module},
                headers=self._headers(),
            )
        body = self._handle(resp, f"GET /settings/fields?module={module}")
        fields = body.get("fields", [])

        todoist_field_api_name: str | None = None
        status_values: list[str] = []

        for field in fields:
            # Custom field discovery — use field_label (NOT display_value — see Pitfall 6).
            if field.get("custom_field") and "Todoist" in (field.get("field_label") or ""):
                todoist_field_api_name = field.get("api_name")

            if field.get("api_name") == "Status" and field.get("data_type") == "picklist":
                status_values = [
                    pv["actual_value"]
                    for pv in field.get("pick_list_values", [])
                    if "actual_value" in pv
                ]

        log.info(
            "zoho_field_resolved",
            module=module,
            todoist_task_id_api_name=todoist_field_api_name,
            status_picklist_values=status_values,
        )
        return {
            "todoist_task_id_api_name": todoist_field_api_name,
            "status_picklist_values": status_values,
        }

    async def fetch_tasks_modified_since(
        self,
        since: datetime,
        owner_id: str,
    ) -> list[dict[str, Any]]:
        """
        Search Zoho Tasks where Modified_Time >= since AND Owner == owner_id.
        Returns a flat list of raw record dicts, concatenated across all pages.
        - Timestamp is always formatted as ISO8601 with explicit +00:00 UTC offset.
        - Criteria ALWAYS includes both Modified_Time and Owner — never a full scan.
        - HTTP 204 is treated as valid empty result (returns []).
        - Pagination terminates when info.more_records is False.

        NOTE: this method is all-or-nothing. If any page request fails, the exception
        propagates immediately and any already-accumulated records are discarded.
        Callers should retry the full call on ZohoRateLimitError / ZohoAPIError.
        """
        since_str = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        criteria = (
            f"((Modified_Time:greater_equal:{since_str})"
            f"and(Owner:equals:{owner_id}))"
        )
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/Tasks/search",
                    params={"criteria": criteria, "page": page, "per_page": 200},
                    headers=self._headers(),
                )
            if resp.status_code == 204:
                # Valid empty result — DO NOT call _handle.
                break
            body = self._handle(resp, f"GET /Tasks/search page={page}")
            results.extend(body.get("data", []))
            info = body.get("info") or {}
            if not info.get("more_records"):
                break
            page += 1
        return results
