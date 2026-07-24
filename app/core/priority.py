# app/core/priority.py

# Zoho priority string → Todoist priority integer
ZOHO_TO_TODOIST: dict[str | None, int] = {
    "Highest": 4,   # p1 / urgent (red)
    "High":    3,   # p2 (orange)
    "Normal":  2,   # p3 (blue)
    "Low":     1,   # p4 (no colour)
    "Lowest":  1,   # collapses to Low; known data loss, documented
    None:      1,   # unset priority → no priority
    "":        1,   # empty string → no priority
}

# Todoist priority integer → Zoho priority string
TODOIST_TO_ZOHO: dict[int, str] = {
    4: "Highest",
    3: "High",
    2: "Normal",
    1: "Low",     # Todoist p4 → Zoho Low (Lowest lost in round-trip)
}


def zoho_to_todoist_priority(zoho_priority: str | None) -> int:
    return ZOHO_TO_TODOIST.get(zoho_priority, 1)


def todoist_to_zoho_priority(todoist_priority: int) -> str:
    return TODOIST_TO_ZOHO.get(todoist_priority, "Low")


# Todoist-int (canonical) priority -> Nirvana (state, starred) two-axis pair.
# D-05: Focus (starred) is independent of GTD state, not folded into a single enum.
TODOIST_TO_NIRVANA: dict[int, tuple[str, bool]] = {
    4: ("next", True),        # Highest -> Focus + next
    3: ("next", False),       # High -> next
    2: ("scheduled", False),  # Normal -> scheduled
    1: ("someday", False),    # Low -> someday
}

# Nirvana GTD states that map to a "next"-equivalent priority (3) when NOT starred.
_NIRVANA_STATE_NEXT_EQUIVALENT: set[str] = {"next"}
# Nirvana GTD states that map to a "scheduled"-equivalent priority (2) when NOT starred.
_NIRVANA_STATE_SCHEDULED_EQUIVALENT: set[str] = {"scheduled", "waiting"}


def todoist_priority_to_nirvana(todoist_priority: int) -> tuple[str, bool]:
    """Canonical Todoist-int (1-4) -> Nirvana (state, starred). Unknown ints fall back
    to the Low bucket, mirroring todoist_to_zoho_priority's .get(x, "Low") pattern."""
    return TODOIST_TO_NIRVANA.get(todoist_priority, ("someday", False))


def nirvana_to_todoist_priority(state: str | None, starred: bool) -> int:
    """Nirvana (state, starred) -> canonical Todoist-int (1-4).

    D-06: `state` is an open/unenumerated vocabulary (e.g. 'recurring' observed in
    spike 002, not in get_task_counts' summary keys). Unknown states MUST default
    safely to Low (1), never raise. `starred` (Focus) always wins over state per D-05.
    """
    if starred:
        return 4
    if state in _NIRVANA_STATE_NEXT_EQUIVALENT:
        return 3
    if state in _NIRVANA_STATE_SCHEDULED_EQUIVALENT:
        return 2
    return 1
