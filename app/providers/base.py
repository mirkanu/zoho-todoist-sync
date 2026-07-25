"""TaskProvider: the structural interface both TodoistClient and NirvanaClient
satisfy (D-01). Selected at startup via TASK_PROVIDER env var -> Settings.task_provider.

typing.Protocol (not abc.ABC) chosen deliberately: this codebase has zero existing
class hierarchies (TodoistClient has no base class, writer.py is standalone
functions) — structural typing lets both concrete clients satisfy this interface
without an inheritance rewrite of working, shipped code.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.core.normalise import NormalisedTask


class TaskProvider(Protocol):
    async def fetch(self, external_id: str) -> "NormalisedTask":
        """Fetch one task by its external (provider-side) id. Raises a typed
        NotFoundError subclass if the task does not exist — never returns None."""
        ...

    async def create(
        self, normalised: "NormalisedTask", zoho_task_id: str, description: str | None = None
    ) -> str:
        """Create a new task on the provider. Returns the new external id.
        `description` is Todoist-specific (DESC-1..4, existing shipped behavior —
        writes a Zoho-link/deal-context description); Nirvana implementations accept
        and ignore it (Nirvana has no description field, out of scope per CONTEXT.md)."""
        ...

    async def update(self, external_id: str, normalised: "NormalisedTask") -> None:
        """Update title/due_date/priority on an existing task."""
        ...

    async def complete(self, external_id: str) -> None:
        """Mark an existing task complete."""
        ...

    async def delete(self, external_id: str, task_name: str | None = None) -> None:
        """Delete (or soft-delete) an existing task. Sends a Resend notification."""
        ...

    async def close(self) -> None:
        """Release any held HTTP resources (called on shutdown)."""
        ...


def get_provider(settings: "Settings") -> TaskProvider:
    """Factory: instantiate the TaskProvider selected by settings.task_provider.

    Called once at startup (app/main.py lifespan, app/worker/settings.py on_startup).
    """
    if settings.task_provider == "todoist":
        from app.todoist.client import TodoistClient

        return TodoistClient(api_token=settings.todoist_api_token)
    if settings.task_provider == "nirvana":
        from app.nirvana.client import NirvanaClient

        return NirvanaClient(pat=settings.nirvana_pat)
    raise ValueError(
        f"Unknown TASK_PROVIDER: {settings.task_provider!r} — must be 'todoist' or 'nirvana'"
    )
