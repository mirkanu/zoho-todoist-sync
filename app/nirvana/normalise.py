"""Nirvana-side normalisation. Pure functions only — no I/O."""
from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title

# Placeholder — always overridden by app.worker.jobs._execute_sync, which
# replaces this with zoho_norm.priority before hashing (priority is a
# non-signal for Nirvana; see app/core/priority.py). The value here never
# reaches canonical_hash() unmodified in real sync_task execution, but
# nirvana_task_to_normalised must still return a valid NormalisedTask for
# direct/unit-test callers.
_IGNORED_PRIORITY_PLACEHOLDER = 1


def nirvana_task_to_normalised(task: dict) -> NormalisedTask:
    """Convert a raw Nirvana get_tasks() dict to NormalisedTask.

    D-08: only `duedate` maps to due_date. Nirvana's distinct scheduled-start
    field is never read — no Zoho equivalent, deferred indefinitely.
    D-07: is_completed is derived from `completed` being a non-null date string,
    never compared as a boolean (Nirvana's `completed` reads back as "YYYY-MM-DD").
    Priority: Nirvana has no priority mapping (2026-07-28 decision) — the
    placeholder value here is replaced by the caller (app.worker.jobs) with
    Zoho's own priority before hashing, so priority never causes a
    Nirvana-Zoho divergence.
    """
    return NormalisedTask(
        title=normalise_title(task.get("name")),
        due_date=normalise_due_date(task.get("duedate")),
        priority=_IGNORED_PRIORITY_PLACEHOLDER,
        is_completed=task.get("completed") is not None,
    )
