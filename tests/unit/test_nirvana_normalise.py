from app.nirvana.normalise import nirvana_task_to_normalised


def test_normalise_basic_with_duedate():
    task = {"name": "Buy milk", "state": "next", "starred": False, "duedate": "2026-08-01"}
    result = nirvana_task_to_normalised(task)
    assert result.title == "Buy milk"
    assert result.due_date == "2026-08-01"
    assert result.is_completed is False


def test_normalise_no_duedate_key():
    task = {"name": "X", "state": "next", "starred": True}
    result = nirvana_task_to_normalised(task)
    assert result.due_date is None


def test_normalise_priority_is_a_placeholder_always_overridden_by_caller():
    """2026-07-28 decision: Nirvana has no priority mapping. The value here
    is always replaced by app.worker.jobs._execute_sync with zoho_norm.priority
    before hashing — see that module's docstring/comment for why."""
    task = {"name": "X", "state": "next", "starred": True}
    result = nirvana_task_to_normalised(task)
    assert isinstance(result.priority, int)


def test_normalise_completed_date_string_is_true():
    task = {"name": "X", "state": "someday", "starred": False, "completed": "2026-07-23"}
    result = nirvana_task_to_normalised(task)
    assert result.is_completed is True
    assert isinstance(result.is_completed, bool)


def test_normalise_no_completed_key_is_false():
    task = {"name": "X", "state": "someday", "starred": False}
    result = nirvana_task_to_normalised(task)
    assert result.is_completed is False


def test_normalise_never_reads_startdate():
    task = {
        "name": "X",
        "state": "scheduled",
        "starred": False,
        "duedate": "2026-08-01",
        "startdate": "2026-07-25",
    }
    result = nirvana_task_to_normalised(task)
    assert result.due_date == "2026-08-01"


def test_normalise_unknown_state_does_not_raise():
    task = {"name": "X", "state": "recurring", "starred": False}
    result = nirvana_task_to_normalised(task)
    assert isinstance(result.priority, int)
