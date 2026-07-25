"""Nirvana write operations: create / update / complete / delete (soft-delete via trash).

Mirrors app.todoist.writer conventions:
- Standalone async functions taking a NirvanaClient instance
- Resend email notification on delete (mirrors EDGE-6 pattern)
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.normalise import NormalisedTask
from app.core.notifications import send_deletion_notification
from app.core.priority import todoist_priority_to_nirvana

log = get_logger(__name__)


async def create_nirvana_task(
    normalised: NormalisedTask,
    zoho_task_id: str,
    client: "NirvanaClient",
) -> str:
    """Create a Nirvana task. Returns new task ID as a string.

    NOTE: create_tasks' exact result JSON shape was not captured verbatim in any
    spike transcript — this defensively handles the plausible shapes (a list of
    created items, or a dict with a 'tasks'/'created' key) and raises a clear
    NirvanaAPIError with the raw result logged if none match, rather than guessing
    silently. Verified/adjusted against a live call in Plan 09-07 Task 2 (this
    phase's live Nirvana smoke test) rather than deferred to a nonexistent plan.
    """
    from app.nirvana.client import NirvanaAPIError  # lazy import — avoids circularity

    state, starred = todoist_priority_to_nirvana(normalised.priority)
    item: dict = {"name": normalised.title, "state": state, "starred": starred}
    if normalised.due_date is not None:
        item["duedate"] = normalised.due_date

    result = await client.create_tasks([item])
    created = _extract_created_items(result)
    if not created:
        raise NirvanaAPIError(f"create_tasks returned no parseable created item: {result!r}")
    new_id = str(created[0]["id"])
    log.info("nirvana_task_created", zoho_id=zoho_task_id, nirvana_id=new_id)
    return new_id


def _extract_created_items(result) -> list[dict]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("tasks", "created", "items"):
            if key in result and isinstance(result[key], list):
                return result[key]
        if "id" in result:
            return [result]
    return []


async def update_nirvana_task(
    external_id: str,
    normalised: NormalisedTask,
    client: "NirvanaClient",
) -> None:
    """Update name/state/starred/duedate. duedate is OMITTED (not sent as null) when
    normalised.due_date is None — Nirvana's null-clearing behavior for duedate is
    unverified; omission is the conservative choice. See Task 2 docstring above."""
    state, starred = todoist_priority_to_nirvana(normalised.priority)
    update: dict = {"id": external_id, "name": normalised.title, "state": state, "starred": starred}
    if normalised.due_date is not None:
        update["duedate"] = normalised.due_date
    await client.update_tasks([update])
    log.info("nirvana_task_updated", nirvana_id=external_id)


async def complete_nirvana_task(external_id: str, client: "NirvanaClient") -> None:
    """D-07: writes completed=True; Nirvana persists it as a date string on read-back."""
    await client.update_tasks([{"id": external_id, "completed": True}])
    log.info("nirvana_task_completed", nirvana_id=external_id)


async def delete_nirvana_task(
    external_id: str,
    client: "NirvanaClient",
    task_name: str | None = None,
) -> None:
    """Soft-delete via state='trash' (spike 003: all other fields remain intact)."""
    await client.update_tasks([{"id": external_id, "state": "trash"}])
    log.info("nirvana_task_trashed", nirvana_id=external_id)
    display_name = task_name or external_id
    name_line = (
        f"<p><strong>Task:</strong> {task_name} <code>({external_id})</code></p>"
        if task_name else f"<p><strong>Task ID:</strong> <code>{external_id}</code></p>"
    )
    await send_deletion_notification(
        subject=f"[zoho-todoist-sync] Nirvana task deleted: {display_name}",
        html=(
            f"{name_line}"
            f"<p>The Nirvana task was moved to trash by the sync service because the "
            f"corresponding Zoho task was deleted or reassigned.</p>"
        ),
    )
