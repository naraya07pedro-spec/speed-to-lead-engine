from app.security import sign_body, twilio_signature, verify_form_signature, verify_twilio

SECRET = "s3cret"
BODY = b'{"phone":"5125550142"}'


def test_valid_signature_passes():
    ts = "1780000000"
    sig = sign_body(SECRET, BODY, ts)
    assert verify_form_signature(SECRET, BODY, sig, ts, now=1780000010)


def test_tampered_body_fails():
    ts = "1780000000"
    sig = sign_body(SECRET, BODY, ts)
    assert not verify_form_signature(SECRET, BODY + b" ", sig, ts, now=1780000010)


def test_old_timestamp_is_rejected_as_replay():
    ts = "1780000000"
    sig = sign_body(SECRET, BODY, ts)
    assert not verify_form_signature(SECRET, BODY, sig, ts, now=1780000000 + 301)


def test_missing_or_garbage_headers_fail():
    assert not verify_form_signature(SECRET, BODY, None, "1")
    assert not verify_form_signature(SECRET, BODY, "sha256=x", "not-a-number")


def test_twilio_documented_example():
    # Example values from Twilio's webhook security documentation.
    token = "12345"
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+12349013030",
        "Digits": "1234",
        "From": "+12349013030",
        "To": "+18005551212",
    }
    assert twilio_signature(token, url, params) == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="
    assert verify_twilio(token, url, params, "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")
    assert not verify_twilio(token, url, params, None)
