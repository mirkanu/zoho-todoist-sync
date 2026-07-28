from scripts.migrate_todoist_labels_to_nirvana import compute_state_and_extra_tags


def test_deferred_with_due_date_becomes_scheduled():
    state, tags = compute_state_and_extra_tags(["Deferred"], "2026-09-01")
    assert state == "scheduled"
    assert tags == []


def test_deferred_without_due_date_becomes_someday():
    state, tags = compute_state_and_extra_tags(["Deferred"], None)
    assert state == "someday"
    assert tags == []


def test_waitingfor_becomes_other_contacts_tag():
    state, tags = compute_state_and_extra_tags(["WaitingFor"], None)
    assert state == "inbox"
    assert tags == ["Other Contacts"]


def test_agenda_becomes_other_contacts_tag():
    state, tags = compute_state_and_extra_tags(["Agenda"], None)
    assert tags == ["Other Contacts"]


def test_waitingfor_and_agenda_do_not_duplicate_other_contacts():
    state, tags = compute_state_and_extra_tags(["WaitingFor", "Agenda"], None)
    assert tags == ["Other Contacts"]


def test_passthrough_label_becomes_identical_tag():
    state, tags = compute_state_and_extra_tags(["Conference"], None)
    assert state == "inbox"
    assert tags == ["Conference"]


def test_epacore_passes_through_as_tag():
    state, tags = compute_state_and_extra_tags(["EPACore"], None)
    assert tags == ["EPACore"]


def test_no_labels_default_inbox_no_extra_tags():
    state, tags = compute_state_and_extra_tags([], "2026-09-01")
    assert state == "inbox"
    assert tags == []


def test_deferred_and_passthrough_together():
    """Not observed in real data, but the logic should handle it: Deferred
    still drives state, the other label still becomes a tag."""
    state, tags = compute_state_and_extra_tags(["Deferred", "Conference"], "2026-09-01")
    assert state == "scheduled"
    assert tags == ["Conference"]
