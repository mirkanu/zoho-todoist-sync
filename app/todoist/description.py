"""Description builder for new Todoist tasks created from Zoho (DESC-1–4)."""

ZOHO_TASK_BASE_URL = "https://crm.zoho.eu/crm/org20100156718/tab/Tasks"
NOT_SYNCED_NOTE = "[Todoist descriptions are not synced back to Zoho]"


def build_task_description(zoho_task_id: str, related_to_name: str | None) -> str:
    """Build the Todoist task description for a newly-created task (DESC-1–4).

    Includes: markdown hyperlink (related name or 'Open in Zoho') and not-synced note.
    Called only at creation time — never on update path (DESC-5).
    """
    url = f"{ZOHO_TASK_BASE_URL}/{zoho_task_id}"
    link_text = related_to_name if related_to_name else "Open in Zoho"
    return f"[{link_text}]({url})\n{NOT_SYNCED_NOTE}"


def _extract_related_to_name(zoho_record: dict) -> str | None:
    """Extract What_Id.name from a raw Zoho Tasks record.

    Returns None if What_Id is absent, null, not a dict, or has empty name.
    Guard against AttributeError (Pitfall 1 from RESEARCH.md).
    """
    what_id = zoho_record.get("What_Id")
    if not isinstance(what_id, dict):
        return None
    return what_id.get("name") or None
