# tests/unit/test_todoist_description.py
from app.todoist.description import build_task_description, _extract_related_to_name


def test_description_with_related_to():
    """DESC-1+2+3: includes Re: line, Zoho URL, and not-synced note."""
    result = build_task_description("Z123", "Acme Deal")
    assert result.startswith("Re: Acme Deal\n")
    assert "crm.zoho.eu" in result
    assert "/tab/Tasks/Z123" in result
    assert "Not synced back to Zoho." in result


def test_description_no_related_to():
    """DESC-4: no Re: line when related_to_name is None."""
    result = build_task_description("Z456", None)
    assert not result.startswith("Re:")
    assert "/tab/Tasks/Z456" in result
    assert "Not synced back to Zoho." in result


def test_description_includes_zoho_url():
    """DESC-2: URL contains the task ID."""
    result = build_task_description("Z789", None)
    assert "Z789" in result


def test_description_includes_not_synced_note():
    """DESC-3: note is always present."""
    result = build_task_description("Z000", "Some name")
    assert "Not synced back to Zoho." in result


def test_extract_related_to_name_from_dict():
    """What_Id is a dict with name key — extracts name."""
    record = {"What_Id": {"name": "Acme Corp", "id": "123"}}
    assert _extract_related_to_name(record) == "Acme Corp"


def test_extract_related_to_name_null():
    """What_Id is null — returns None without AttributeError (Pitfall 1)."""
    record = {"What_Id": None}
    assert _extract_related_to_name(record) is None


def test_extract_related_to_name_missing():
    """What_Id key absent — returns None."""
    record = {}
    assert _extract_related_to_name(record) is None


def test_extract_related_to_name_empty_string():
    """What_Id.name is empty string — returns None (falsy guard)."""
    record = {"What_Id": {"name": "", "id": "123"}}
    assert _extract_related_to_name(record) is None
