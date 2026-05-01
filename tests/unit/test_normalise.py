# tests/unit/test_normalise.py
from app.core.normalise import normalise_due_date, normalise_title

def test_date_only_passthrough():
    assert normalise_due_date("2026-05-01") == "2026-05-01"

def test_datetime_with_tz_positive():
    assert normalise_due_date("2026-05-01T00:00:00+05:30") == "2026-05-01"

def test_datetime_with_tz_negative():
    assert normalise_due_date("2026-05-01T00:00:00-03:00") == "2026-05-01"

def test_none_due_date():
    assert normalise_due_date(None) is None

def test_empty_due_date():
    assert normalise_due_date("") is None

def test_title_crlf():
    assert normalise_title("Line\r\nTwo") == "Line\nTwo"

def test_title_strip():
    assert normalise_title("  padded  ") == "padded"

def test_title_nfc():
    import unicodedata
    nfd = unicodedata.normalize("NFD", "café")
    assert normalise_title(nfd) == "café"
