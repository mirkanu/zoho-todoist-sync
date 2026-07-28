"""Note builder for new Nirvana tasks created from Zoho.

Mirrors app.todoist.description's intent (link back to Zoho) but supports
any related Zoho module generically (via $se_module), and links both the
related record and the Zoho Task record itself — unlike the Todoist
description, which only links the task and borrows the related record's
name as link text.
"""
from app.core.config import get_settings

NOT_SYNCED_NOTE = "[Nirvana notes are not synced back to Zoho]"


def _zoho_record_url(module: str, record_id: str) -> str:
    s = get_settings()
    return f"https://crm.zoho.eu/crm/{s.zoho_org_id}/tab/{module}/{record_id}"


def _extract_related_link(zoho_record: dict) -> str | None:
    """Build a markdown link to the related Zoho record (any module), using
    its name as link text. Returns None if What_Id, its name/id, or the
    module (from $se_module) are missing — better to omit the line than
    link to a wrong/broken module path."""
    what_id = zoho_record.get("What_Id")
    if not isinstance(what_id, dict):
        return None
    name = what_id.get("name")
    related_id = what_id.get("id")
    module = zoho_record.get("$se_module")
    if not name or not related_id or not module:
        return None
    return f"[{name}]({_zoho_record_url(module, related_id)})"


def build_task_note(zoho_task_id: str, zoho_record: dict) -> str:
    """Build the Nirvana task note for a newly-created task.

    Line 1 (if a related record exists): link to that record, any module.
    Line 2 (always): link to the Zoho Task record itself.
    Line 3 (always): not-synced-back disclaimer.
    Called only at creation time — never on update (mirrors Todoist's DESC-5 rule).
    """
    lines: list[str] = []
    related_link = _extract_related_link(zoho_record)
    if related_link is not None:
        lines.append(related_link)
    lines.append(f"[Open Zoho Task]({_zoho_record_url('Tasks', zoho_task_id)})")
    lines.append(NOT_SYNCED_NOTE)
    return "\n".join(lines)
