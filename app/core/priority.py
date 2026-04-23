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
