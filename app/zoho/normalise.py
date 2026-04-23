# app/zoho/normalise.py
# Adapter: raw Zoho Tasks record dict -> NormalisedTask (LOOP-2 rules from Phase 1).
from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title
from app.core.priority import zoho_to_todoist_priority


def zoho_record_to_normalised(
    record: dict,
    terminal_statuses: list[str],
) -> NormalisedTask:
    """
    Convert a raw Zoho Tasks record dict (inner dict from response['data'][0])
    to a NormalisedTask value object suitable for canonical hashing.

    - title: Subject field, whitespace-stripped (via normalise_title)
    - due_date: Due_Date normalised to YYYY-MM-DD or None
    - priority: Zoho priority string mapped to Todoist integer 1..4
    - is_completed: True iff Status is in terminal_statuses list
    """
    return NormalisedTask(
        title=normalise_title(record.get("Subject") or ""),
        due_date=normalise_due_date(record.get("Due_Date")),
        priority=zoho_to_todoist_priority(record.get("Priority")),
        is_completed=record.get("Status") in terminal_statuses,
    )
