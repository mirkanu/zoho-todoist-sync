"""Todoist write operations: create / update / complete / delete.

Mirrors app.todoist.client conventions:
- Standalone async functions (not a class)
- Typed exceptions imported from app.todoist.client (not redefined)
- _raise_typed maps httpx.HTTPStatusError.status_code to typed exception
- Resend email notifications on delete (fire-and-forget, EDGE-6)
"""
from __future__ import annotations

from datetime import date
from typing import NoReturn

import httpx
from todoist_api_python.api_async import TodoistAPIAsync

from app.core.logging import get_logger
from app.core.normalise import NormalisedTask
from app.core.notifications import send_deletion_notification
from app.todoist.client import (
    TodoistAPIError,
    TodoistAuthError,
    TodoistNotFoundError,
    TodoistRateLimitError,
)

log = get_logger(__name__)


def _raise_typed(status: int, context: str, cause: Exception) -> NoReturn:
    if status == 401:
        raise TodoistAuthError(f"401 Unauthorized — {context}") from cause
    if status == 404:
        raise TodoistNotFoundError(f"404 Not Found — {context}") from cause
    if status == 429:
        raise TodoistRateLimitError(f"429 Rate limit — {context}") from cause
    raise TodoistAPIError(f"{status} — {context}") from cause


async def create_todoist_task(
    normalised: NormalisedTask,
    zoho_task_id: str,
    todoist_api: TodoistAPIAsync,
) -> str:
    """SYNC-1/2/8: create a Todoist task with [zoho:ID] footer. Returns new task ID."""
    from app.core.config import get_settings  # lazy import — avoids module-level Settings() call
    due = date.fromisoformat(normalised.due_date) if normalised.due_date else None
    try:
        task = await todoist_api.add_task(
            content=normalised.title,
            description=f"\n\n---\n[zoho:{zoho_task_id}]",
            project_id=get_settings().todoist_project_id,
            due_date=due,         # date object or None; SDK formats to YYYY-MM-DD
            priority=normalised.priority,
            # labels intentionally omitted (SYNC-9)
        )
    except httpx.HTTPStatusError as exc:
        _raise_typed(exc.response.status_code, f"add_task zoho:{zoho_task_id}", exc)
    except Exception:
        raise  # let unexpected errors propagate cleanly without unbound `task`
    log.info("todoist_task_created", zoho_id=zoho_task_id, todoist_id=task.id)
    return task.id


async def update_todoist_task(
    task_id: str,
    normalised: NormalisedTask,
    todoist_api: TodoistAPIAsync,
) -> None:
    """SYNC-2 + EDGE-3: update content/priority/due. Never touches description or labels."""
    kwargs: dict = {
        "content": normalised.title,
        "priority": normalised.priority,
        # description: NEVER pass — would destroy [zoho:ID] footer (Pitfall 6)
        # labels: NEVER pass — SYNC-9
    }
    if normalised.due_date is not None:
        kwargs["due_date"] = date.fromisoformat(normalised.due_date)
    else:
        # CRITICAL (Pitfall 1): SDK's kwargs_without_none drops due_date=None silently.
        # The only way to clear a Todoist due date through the SDK is due_string="no date".
        kwargs["due_string"] = "no date"
    try:
        await todoist_api.update_task(task_id, **kwargs)
    except httpx.HTTPStatusError as exc:
        _raise_typed(exc.response.status_code, f"update_task {task_id}", exc)
    log.info("todoist_task_updated", todoist_id=task_id)


async def complete_todoist_task(task_id: str, todoist_api: TodoistAPIAsync) -> None:
    """EDGE-7: close the task via SDK complete_task → POST /tasks/{id}/close."""
    try:
        await todoist_api.complete_task(task_id)
    except httpx.HTTPStatusError as exc:
        _raise_typed(exc.response.status_code, f"complete_task {task_id}", exc)
    log.info("todoist_task_completed", todoist_id=task_id)


async def delete_todoist_task(task_id: str, todoist_api: TodoistAPIAsync) -> None:
    """EDGE-1: delete task + send Resend email. 404 is idempotent (no email). EDGE-6: Resend failure logged."""
    try:
        await todoist_api.delete_task(task_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            log.info("todoist_delete_idempotent", todoist_id=task_id)
            return  # already gone — do NOT send email (Pitfall 5)
        _raise_typed(exc.response.status_code, f"delete_task {task_id}", exc)
    log.info("todoist_task_deleted", todoist_id=task_id)
    await send_deletion_notification(
        subject=f"[zoho-todoist-sync] Todoist task deleted: {task_id}",
        html=f"<p>Todoist task <code>{task_id}</code> was deleted by the sync service "
             f"(propagation from Zoho reassignment or direct deletion).</p>",
    )
