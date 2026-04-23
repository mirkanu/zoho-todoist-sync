# tests/unit/test_hash.py
import re
from app.core.normalise import NormalisedTask, normalise_due_date, normalise_title
from app.core.hash import canonical_hash
from app.core.priority import zoho_to_todoist_priority

def make_task(title="Buy milk", due="2026-05-01", priority=2, completed=False):
    return NormalisedTask(
        title=normalise_title(title),
        due_date=normalise_due_date(due),
        priority=priority,
        is_completed=completed,
    )

def test_same_logical_task_same_hash():
    t1 = make_task(due="2026-05-01T00:00:00+05:30")
    t2 = make_task(due="2026-05-01")
    assert canonical_hash(t1) == canonical_hash(t2)

def test_crlf_same_as_lf():
    t1 = make_task(title="Line one\r\nLine two")
    t2 = make_task(title="Line one\nLine two")
    assert canonical_hash(t1) == canonical_hash(t2)

def test_unicode_nfc_nfd_same_hash():
    import unicodedata
    nfc = "café"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    t1 = make_task(title=nfc)
    t2 = make_task(title=nfd)
    assert canonical_hash(t1) == canonical_hash(t2)

def test_priority_round_trip():
    from app.core.priority import zoho_to_todoist_priority, todoist_to_zoho_priority
    assert zoho_to_todoist_priority("Highest") == 4
    assert todoist_to_zoho_priority(4) == "Highest"
    assert zoho_to_todoist_priority("High") == 3
    assert todoist_to_zoho_priority(3) == "High"
    assert zoho_to_todoist_priority("Normal") == 2
    assert todoist_to_zoho_priority(2) == "Normal"
    assert zoho_to_todoist_priority("Low") == 1
    assert zoho_to_todoist_priority("Lowest") == 1
    assert zoho_to_todoist_priority(None) == 1
    assert zoho_to_todoist_priority("") == 1
    assert todoist_to_zoho_priority(1) == "Low"

def test_null_due_date_stable():
    t1 = make_task(due=None)
    t2 = make_task(due="")
    assert canonical_hash(t1) == canonical_hash(t2)

def test_label_not_in_hash():
    from dataclasses import fields
    field_names = {f.name for f in fields(NormalisedTask)}
    assert "labels" not in field_names
    assert "description" not in field_names

def test_completed_flag_changes_hash():
    t_open = make_task(completed=False)
    t_done = make_task(completed=True)
    assert canonical_hash(t_open) != canonical_hash(t_done)

def test_hash_returns_64_hex_chars():
    t = make_task()
    h = canonical_hash(t)
    assert re.fullmatch(r"[0-9a-f]{64}", h), f"Invalid SHA-256 hex: {h!r}"
