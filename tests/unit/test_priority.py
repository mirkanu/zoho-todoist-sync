# tests/unit/test_priority.py
from app.core.priority import (
    ZOHO_TO_TODOIST, TODOIST_TO_ZOHO,
    zoho_to_todoist_priority, todoist_to_zoho_priority,
)

def test_highest_maps_to_4():
    assert zoho_to_todoist_priority("Highest") == 4

def test_high_maps_to_3():
    assert zoho_to_todoist_priority("High") == 3

def test_normal_maps_to_2():
    assert zoho_to_todoist_priority("Normal") == 2

def test_low_maps_to_1():
    assert zoho_to_todoist_priority("Low") == 1

def test_lowest_maps_to_1():
    assert zoho_to_todoist_priority("Lowest") == 1

def test_none_maps_to_1():
    assert zoho_to_todoist_priority(None) == 1

def test_empty_string_maps_to_1():
    assert zoho_to_todoist_priority("") == 1

def test_unknown_zoho_priority_maps_to_1():
    assert zoho_to_todoist_priority("SomeUnknownValue") == 1

def test_todoist_4_maps_to_highest():
    assert todoist_to_zoho_priority(4) == "Highest"

def test_todoist_3_maps_to_high():
    assert todoist_to_zoho_priority(3) == "High"

def test_todoist_2_maps_to_normal():
    assert todoist_to_zoho_priority(2) == "Normal"

def test_todoist_1_maps_to_low():
    assert todoist_to_zoho_priority(1) == "Low"

def test_round_trip_highest():
    assert todoist_to_zoho_priority(zoho_to_todoist_priority("Highest")) == "Highest"

def test_round_trip_high():
    assert todoist_to_zoho_priority(zoho_to_todoist_priority("High")) == "High"

def test_round_trip_normal():
    assert todoist_to_zoho_priority(zoho_to_todoist_priority("Normal")) == "Normal"

def test_lowest_round_trip_loses_precision():
    # Known data loss: Lowest collapses to Low in round-trip
    assert todoist_to_zoho_priority(zoho_to_todoist_priority("Lowest")) == "Low"

def test_highest_is_NOT_todoist_1():
    """Regression guard: CLAUDE.md says priority is NOT inverted. Highest → 4, NEVER 1."""
    assert zoho_to_todoist_priority("Highest") != 1
    assert zoho_to_todoist_priority("Highest") == 4

def test_zoho_to_todoist_dict_contains_none_key():
    """None must be an explicit key, not a fallback, per SYNC-2 unset handling."""
    assert None in ZOHO_TO_TODOIST
    assert ZOHO_TO_TODOIST[None] == 1

def test_zoho_to_todoist_dict_contains_empty_string_key():
    assert "" in ZOHO_TO_TODOIST
    assert ZOHO_TO_TODOIST[""] == 1
