"""Nirvana-side normalisation. Pure functions only — no I/O."""
from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title
from app.core.priority import nirvana_to_todoist_priority


def nirvana_task_to_normalised(task: dict) -> NormalisedTask:
    """Convert a raw Nirvana get_tasks() dict to NormalisedTask.

    D-08: only `duedate` maps to due_date. Nirvana's distinct scheduled-start
    field is never read — no Zoho equivalent, deferred indefinitely.
    D-07: is_completed is derived from `completed` being a non-null date string,
    never compared as a boolean (Nirvana's `completed` reads back as "YYYY-MM-DD").
    D-06: `state` is open-vocabulary — nirvana_to_todoist_priority handles unknown
    values defensively (never raises).
    """
    return NormalisedTask(
        title=normalise_title(task.get("name")),
        due_date=normalise_due_date(task.get("duedate")),
        priority=nirvana_to_todoist_priority(task.get("state"), bool(task.get("starred", False))),
        is_completed=task.get("completed") is not None,
    )
