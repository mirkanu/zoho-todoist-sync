"""Todoist-side normalisation and footer parsing.

Pure functions only — no I/O. Imported by client.py and sync_manager.py.
"""
from app.core.normalise import ZOHO_ID_RE


def extract_zoho_id(description: str | None) -> str | None:
    """Parse [zoho:(\\d+)] from anywhere in the description.

    Returns the Zoho task ID as a string, or None if no footer is present.
    Works if the footer is at end, mid-text, or after user edits above it.

    SYNC-5: regex must match digits only; non-digit content after `zoho:` returns None.
    SYNC-8 precondition: caller uses `None` return to discard footerless tasks.
    """
    if not description:
        return None
    m = ZOHO_ID_RE.search(description)
    return m.group(1) if m else None
