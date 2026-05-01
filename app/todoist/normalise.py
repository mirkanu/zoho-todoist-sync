"""Todoist-side normalisation.

Pure functions only — no I/O. Imported by client.py and sync_manager.py.
"""
from todoist_api_python.models import Task

from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title


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
