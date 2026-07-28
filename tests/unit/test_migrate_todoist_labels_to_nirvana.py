from scripts.migrate_todoist_labels_to_nirvana import compute_state_and_extra_tags


def test_deferred_with_due_date_becomes_scheduled_with_matching_start_date():
    """Nirvana requires startdate whenever state="scheduled" — the task is
    scheduled to its own due date (2026-07-28 decision)."""
    state, tags, start_date = compute_state_and_extra_tags(["Deferred"], "2026-09-01")
    assert state == "scheduled"
    assert start_date == "2026-09-01"
    assert tags == []


def test_deferred_without_due_date_becomes_someday_no_start_date():
    state, tags, start_date = compute_state_and_extra_tags(["Deferred"], None)
    assert state == "someday"
    assert start_date is None
    assert tags == []


def test_waitingfor_becomes_other_contacts_tag():
    state, tags, start_date = compute_state_and_extra_tags(["WaitingFor"], None)
    assert state == "inbox"
    assert start_date is None
    assert tags == ["Other Contacts"]


def test_agenda_becomes_other_contacts_tag():
    state, tags, start_date = compute_state_and_extra_tags(["Agenda"], None)
    assert tags == ["Other Contacts"]


def test_waitingfor_and_agenda_do_not_duplicate_other_contacts():
    state, tags, start_date = compute_state_and_extra_tags(["WaitingFor", "Agenda"], None)
    assert tags == ["Other Contacts"]


def test_passthrough_label_becomes_identical_tag():
    state, tags, start_date = compute_state_and_extra_tags(["Conference"], None)
    assert state == "inbox"
    assert start_date is None
    assert tags == ["Conference"]


def test_epacore_passes_through_as_tag():
    state, tags, start_date = compute_state_and_extra_tags(["EPACore"], None)
    assert tags == ["EPACore"]


def test_no_labels_default_inbox_no_extra_tags():
    state, tags, start_date = compute_state_and_extra_tags([], "2026-09-01")
    assert state == "inbox"
    assert start_date is None
    assert tags == []


def test_deferred_and_passthrough_together():
    """Not observed in real data, but the logic should handle it: Deferred
    still drives state+start_date, the other label still becomes a tag."""
    state, tags, start_date = compute_state_and_extra_tags(["Deferred", "Conference"], "2026-09-01")
    assert state == "scheduled"
    assert start_date == "2026-09-01"
    assert tags == ["Conference"]
