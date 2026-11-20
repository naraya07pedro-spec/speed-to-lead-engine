import pytest

from app.normalize import clean_text, idempotency_key, normalize_email, normalize_phone


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("(512) 555-0142", "+15125550142"),
        ("512.555.0142", "+15125550142"),
        ("+1 512 555 0142", "+15125550142"),
        ("1-512-555-0142", "+15125550142"),
        ("+62 812 3456 7890", "+6281234567890"),
        ("555-0142", None),
        ("(012) 555-0142", None),  # area code cannot start with 0
        ("(512) 155-0142", None),  # exchange cannot start with 1
        ("", None),
        (None, None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_normalize_email():
    assert normalize_email("  Maria.G@Example.COM ") == "maria.g@example.com"
    assert normalize_email("not-an-email") is None
    assert normalize_email(None) is None


def test_clean_text_collapses_whitespace_and_truncates():
    assert clean_text("  AC \n\n not   working ") == "AC not working"
    assert len(clean_text("x" * 5000)) == 2000


def test_idempotency_key_prefers_external_id():
    a = idempotency_key("missed_call", "CA123", "+15125550142")
    b = idempotency_key("missed_call", "CA123", "+15125559999")
    assert a == b


def test_idempotency_key_hashes_payload_without_id():
    a = idempotency_key("web_form", None, "+15125550142", None, "leak")
    b = idempotency_key("web_form", None, "+15125550142", None, "leak")
    c = idempotency_key("web_form", None, "+15125550142", None, "different")
    assert a == b != c
