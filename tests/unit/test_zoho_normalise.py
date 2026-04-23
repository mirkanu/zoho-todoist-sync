# tests/unit/test_zoho_normalise.py
from app.zoho.normalise import zoho_record_to_normalised


def test_zoho_record_to_normalised_basic():
    record = {
        "Subject": "  Buy milk  ",
        "Due_Date": "2026-05-01",
        "Priority": "High",
        "Status": "Not Started",
    }
    result = zoho_record_to_normalised(record, terminal_statuses=["Completed"])
    assert result.title == "Buy milk"
    assert result.due_date == "2026-05-01"
    assert result.priority == 3   # High -> 3
    assert result.is_completed is False

def test_zoho_record_to_normalised_completed_status():
    record = {"Subject": "Done task", "Due_Date": None, "Priority": "Normal", "Status": "Completed"}
    result = zoho_record_to_normalised(record, terminal_statuses=["Completed"])
    assert result.is_completed is True

def test_zoho_record_to_normalised_custom_terminal_status():
    record = {"Subject": "Done", "Due_Date": None, "Priority": None, "Status": "Closed"}
    result = zoho_record_to_normalised(record, terminal_statuses=["Completed", "Closed"])
    assert result.is_completed is True

def test_zoho_record_to_normalised_null_due_date():
    record = {"Subject": "No date", "Due_Date": None, "Priority": "Low", "Status": "Open"}
    result = zoho_record_to_normalised(record, terminal_statuses=["Completed"])
    assert result.due_date is None
    assert result.priority == 1
    assert result.is_completed is False

def test_zoho_record_to_normalised_datetime_due_date_normalised():
    record = {"Subject": "t", "Due_Date": "2026-05-01T00:00:00+05:30", "Priority": "Normal", "Status": "Open"}
    result = zoho_record_to_normalised(record, terminal_statuses=["Completed"])
    assert result.due_date == "2026-05-01"
