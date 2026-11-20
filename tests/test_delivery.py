import base64
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs

import pytest

from app.config import Settings
from app.delivery import (
    DeliveryError,
    HttpResponse,
    HubSpotCRM,
    TwilioNotifier,
    build_crm,
    build_notifier,
    with_retries,
)
from app.store import Lead


class FakeTransport:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict, bytes | None]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)


def sample_lead(**kw) -> Lead:
    data = dict(
        id=1,
        source="web_form",
        name="Maria Garcia",
        phone="+15125550142",
        email="maria@example.com",
        message="No AC",
        urgency="emergency",
        urgency_reason="no cooling",
        received_at=datetime.now(timezone.utc),
        auto_replied_at=None,
        human_touched_at=None,
        escalated_at=None,
        crm_id=None,
        duplicate_count=0,
    )
    data.update(kw)
    return Lead(**data)


def test_twilio_request_shape():
    t = FakeTransport(HttpResponse(201, json.dumps({"sid": "SM123"})))
    sid = TwilioNotifier("AC1", "tok", "+15125550100", transport=t).send("+15125550142", "hello")
    assert sid == "SM123"
    method, url, headers, body = t.calls[0]
    assert method == "POST" and url.endswith("/Accounts/AC1/Messages.json")
    assert base64.b64decode(headers["Authorization"].split()[1]) == b"AC1:tok"
    assert parse_qs(body.decode()) == {"To": ["+15125550142"], "From": ["+15125550100"], "Body": ["hello"]}


@pytest.mark.parametrize("status, retryable", [(500, True), (503, True), (429, True), (400, False), (401, False)])
def test_twilio_error_classification(status, retryable):
    t = FakeTransport(HttpResponse(status, "{}"))
    with pytest.raises(DeliveryError) as exc:
        TwilioNotifier("AC1", "tok", "+15125550100", transport=t).send("+1", "x")
    assert exc.value.retryable is retryable


def test_with_retries_backs_off_exponentially():
    delays: list[float] = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise DeliveryError("boom")
        return "ok"

    assert with_retries(fn, attempts=3, base_delay=0.5, sleep=delays.append) == ("ok", 3)
    assert delays == [0.5, 1.0]


def test_hubspot_creates_contact():
    t = FakeTransport(HttpResponse(201, json.dumps({"id": "901"})))
    assert HubSpotCRM("pat", transport=t).upsert_contact(sample_lead()) == "901"
    props = json.loads(t.calls[0][3])["properties"]
    assert props["firstname"] == "Maria" and props["lastname"] == "Garcia"
    assert props["phone"] == "+15125550142"


def test_hubspot_conflict_falls_back_to_update_by_email():
    t = FakeTransport(HttpResponse(409, "{}"), HttpResponse(200, json.dumps({"id": "77"})))
    assert HubSpotCRM("pat", transport=t).upsert_contact(sample_lead()) == "77"
    method, url, _, _ = t.calls[1]
    assert method == "PATCH" and url.endswith("/contacts/maria%40example.com?idProperty=email")


def test_factories_fail_fast_on_missing_credentials():
    with pytest.raises(RuntimeError, match="TWILIO_AUTH_TOKEN"):
        build_notifier(Settings(notifier="twilio", twilio_account_sid="AC1", twilio_from_number="+1"))
    with pytest.raises(RuntimeError, match="HUBSPOT_TOKEN"):
        build_crm(Settings(crm="hubspot"))
