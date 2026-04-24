"""Todoist-side normalisation and footer parsing.

Pure functions only — no I/O. Imported by client.py and sync_manager.py.
"""
from todoist_api_python.models import Task

from app.core.normalise import ZOHO_ID_RE, NormalisedTask, normalise_due_date, normalise_title


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


def todoist_task_to_normalised(task: Task) -> NormalisedTask:
    """Convert a Todoist SDK Task to NormalisedTask.

    SYNC-9: labels are NEVER read from the Task — excluded structurally
    (NormalisedTask has no labels field) and behaviourally (not accessed here).
    Due dates (date or datetime) are stringified then normalised; str(date)
    produces "YYYY-MM-DD" and str(datetime) produces "YYYY-MM-DD HH:MM:SS",
    both accepted by normalise_due_date via datetime.fromisoformat.
    """
    raw_due: str | None = None
    if task.due is not None:
        raw_due = str(task.due.date)
    return NormalisedTask(
        title=normalise_title(task.content),
        due_date=normalise_due_date(raw_due),
        priority=task.priority,
        is_completed=task.is_completed,
    )
