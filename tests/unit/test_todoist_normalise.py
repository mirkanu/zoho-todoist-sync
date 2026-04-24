import dataclasses
from datetime import date, datetime
from unittest.mock import MagicMock

from app.core.normalise import NormalisedTask
from app.todoist.normalise import extract_zoho_id, todoist_task_to_normalised


def test_extract_zoho_id_none():
    assert extract_zoho_id(None) is None


def test_extract_zoho_id_empty():
    assert extract_zoho_id("") is None


def test_extract_zoho_id_missing():
    assert extract_zoho_id("Just a title") is None


def test_extract_zoho_id_footer_at_end():
    assert extract_zoho_id("Title\n\n---\n[zoho:12345]") == "12345"


def test_extract_zoho_id_mid_text():
    assert extract_zoho_id("[zoho:99] in the middle of text") == "99"


def test_extract_zoho_id_after_user_edit():
    assert extract_zoho_id("User edited body\n\n---\n[zoho:12345]") == "12345"


def test_extract_zoho_id_non_digit():
    assert extract_zoho_id("Text\n[zoho:abc]") is None


def test_extract_zoho_id_empty_id():
    assert extract_zoho_id("Text\n[zoho:]") is None


def test_extract_zoho_id_returns_str():
    assert isinstance(extract_zoho_id("[zoho:42]"), str)


# --- todoist_task_to_normalised adapter tests ---

def test_normalise_basic_date_only():
    task = MagicMock()
    task.content = "  Buy milk  "
    task.priority = 3
    task.is_completed = False
    task.due = MagicMock()
    task.due.date = date(2026, 5, 1)
    task.labels = ["urgent"]  # MUST be ignored
    result = todoist_task_to_normalised(task)
    assert result.title == "Buy milk"
    assert result.due_date == "2026-05-01"
    assert result.priority == 3
    assert result.is_completed is False


def test_normalise_datetime_due():
    task = MagicMock()
    task.content = "Meeting"
    task.priority = 4
    task.is_completed = False
    task.due = MagicMock()
    task.due.date = datetime(2026, 5, 1, 12, 0, 0)
    task.labels = None
    result = todoist_task_to_normalised(task)
    assert result.due_date == "2026-05-01"


def test_normalise_no_due():
    task = MagicMock()
    task.content = "Task"
    task.priority = 1
    task.is_completed = False
    task.due = None
    task.labels = None
    result = todoist_task_to_normalised(task)
    assert result.due_date is None


def test_normalise_completed():
    task = MagicMock()
    task.content = "Done"
    task.priority = 2
    task.is_completed = True
    task.due = None
    task.labels = None
    result = todoist_task_to_normalised(task)
    assert result.is_completed is True


def test_normalise_excludes_labels():
    # NormalisedTask is a frozen dataclass without a labels field.
    # Confirm this structurally so any future schema change breaks the test.
    field_names = {f.name for f in dataclasses.fields(NormalisedTask)}
    assert "labels" not in field_names
    assert field_names == {"title", "due_date", "priority", "is_completed"}


def test_normalise_ignores_labels_field():
    task = MagicMock()
    task.content = "Task"
    task.priority = 1
    task.is_completed = False
    task.due = None
    task.labels = ["label-a", "label-b", "label-c"]
    result = todoist_task_to_normalised(task)
    # Result is a NormalisedTask; it has no labels attribute at all.
    assert not hasattr(result, "labels")
