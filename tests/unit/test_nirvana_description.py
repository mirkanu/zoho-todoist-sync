# tests/unit/test_nirvana_description.py
from app.nirvana.description import build_task_note, _extract_related_link


def test_note_with_related_record_any_module(complete_env):
    """Related record links via $se_module (generic — any Zoho module), name as link text."""
    record = {"$se_module": "Potentials", "What_Id": {"name": "Acme Deal", "id": "DEAL1"}}
    result = build_task_note("Z123", record)
    lines = result.split("\n")
    assert lines[0] == "[Acme Deal](https://crm.zoho.eu/crm/test-org/tab/Potentials/DEAL1)"
    assert lines[1] == "[Open Zoho Task](https://crm.zoho.eu/crm/test-org/tab/Tasks/Z123)"
    assert lines[2] == "[Nirvana notes are not synced back to Zoho]"


def test_note_with_different_module(complete_env):
    """Module name is read generically from $se_module — not hardcoded to Deals/Potentials."""
    record = {"$se_module": "Campaigns", "What_Id": {"name": "WCSB12", "id": "CAMP1"}}
    result = build_task_note("Z456", record)
    assert "[WCSB12](https://crm.zoho.eu/crm/test-org/tab/Campaigns/CAMP1)" in result


def test_note_no_related_record(complete_env):
    """No What_Id — only the Zoho Task link and disclaimer, no related-record line."""
    record = {"What_Id": None}
    result = build_task_note("Z789", record)
    lines = result.split("\n")
    assert len(lines) == 2
    assert lines[0] == "[Open Zoho Task](https://crm.zoho.eu/crm/test-org/tab/Tasks/Z789)"
    assert lines[1] == "[Nirvana notes are not synced back to Zoho]"


def test_note_always_includes_zoho_task_link(complete_env):
    record = {}
    result = build_task_note("Z000", record)
    assert "/tab/Tasks/Z000" in result


def test_note_always_includes_not_synced_disclaimer(complete_env):
    record = {"$se_module": "Potentials", "What_Id": {"name": "X", "id": "1"}}
    result = build_task_note("Z1", record)
    assert "[Nirvana notes are not synced back to Zoho]" in result


def test_extract_related_link_missing_se_module_omits_line(complete_env):
    """Better to omit the related-record line than link to a wrong/broken module path."""
    record = {"What_Id": {"name": "Acme Deal", "id": "DEAL1"}}  # no $se_module
    assert _extract_related_link(record) is None


def test_extract_related_link_null_what_id(complete_env):
    record = {"What_Id": None, "$se_module": "Potentials"}
    assert _extract_related_link(record) is None


def test_extract_related_link_missing_what_id(complete_env):
    record = {"$se_module": "Potentials"}
    assert _extract_related_link(record) is None


def test_extract_related_link_empty_name(complete_env):
    record = {"What_Id": {"name": "", "id": "DEAL1"}, "$se_module": "Potentials"}
    assert _extract_related_link(record) is None


def test_extract_related_link_missing_id(complete_env):
    record = {"What_Id": {"name": "Acme Deal"}, "$se_module": "Potentials"}
    assert _extract_related_link(record) is None
