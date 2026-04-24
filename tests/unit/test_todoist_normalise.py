from app.todoist.normalise import extract_zoho_id


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
