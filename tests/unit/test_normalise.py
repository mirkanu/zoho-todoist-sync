# tests/unit/test_normalise.py
from app.core.normalise import normalise_due_date, normalise_title, strip_footer, ZOHO_ID_RE

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

def test_strip_footer_basic():
    desc = "Some task\n\n---\n[zoho:1234567890]"
    assert strip_footer(desc) == "Some task"

def test_strip_footer_none():
    assert strip_footer(None) == ""

def test_strip_footer_no_footer():
    assert strip_footer("Plain description") == "Plain description"

def test_zoho_id_regex():
    desc = "Some text\n\n---\n[zoho:9876543210]"
    m = ZOHO_ID_RE.search(desc)
    assert m is not None
    assert m.group(1) == "9876543210"

def test_zoho_id_regex_no_match():
    assert ZOHO_ID_RE.search("no footer here") is None

def test_title_crlf():
    assert normalise_title("Line\r\nTwo") == "Line\nTwo"

def test_title_strip():
    assert normalise_title("  padded  ") == "padded"

def test_title_nfc():
    import unicodedata
    nfd = unicodedata.normalize("NFD", "café")
    assert normalise_title(nfd) == "café"
