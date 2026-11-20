"""End-to-end HTTP tests. Skipped when the web extras are not installed."""

import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.delivery import ConsoleNotifier, NullCRM  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import sign_body, twilio_signature  # noqa: E402
from app.service import LeadService  # noqa: E402
from app.store import Store  # noqa: E402


def make_client(**overrides):
    settings = Settings(database_path=":memory:", **overrides)
    notifier = ConsoleNotifier()
    service = LeadService(settings, Store(":memory:"), notifier, NullCRM(), sleep=lambda _: None)
    return TestClient(create_app(settings, service)), notifier


def test_health():
    client, _ = make_client()
    assert client.get("/healthz").json()["ok"] is True


def test_form_webhook_creates_and_dedupes():
    client, notifier = make_client()
    payload = {"name": "Maria", "phone": "512-555-0142", "message": "No AC", "submission_id": "s1"}
    first = client.post("/webhooks/form", json=payload)
    assert first.status_code == 202 and first.json()["status"] == "created"
    again = client.post("/webhooks/form", json=payload)
    assert again.json()["status"] == "replay"
    assert len(notifier.outbox) == 1


def test_form_webhook_rejects_lead_without_contact():
    client, _ = make_client()
    assert client.post("/webhooks/form", json={"message": "hi"}).status_code == 422


def test_signed_webhook():
    client, _ = make_client(webhook_secret="k", demo_mode=False)
    body = json.dumps({"phone": "512-555-0142", "message": "leak"}).encode()
    ts = str(int(time.time()))
    ok = client.post(
        "/webhooks/form",
        content=body,
        headers={"X-Signature": sign_body("k", body, ts), "X-Timestamp": ts, "Content-Type": "application/json"},
    )
    assert ok.status_code == 202
    bad = client.post(
        "/webhooks/form",
        content=body,
        headers={"X-Signature": "sha256=bad", "X-Timestamp": ts, "Content-Type": "application/json"},
    )
    assert bad.status_code == 401


def test_unsigned_webhook_refused_outside_demo_mode():
    client, _ = make_client(demo_mode=False)
    assert client.post("/webhooks/form", json={"phone": "512-555-0142"}).status_code == 500


def test_twilio_missed_call_flow():
    client, notifier = make_client(
        verify_twilio_signature=True, twilio_auth_token="tok", public_base_url="https://leads.example.com"
    )
    params = {"CallSid": "CA1", "From": "+15125550142", "CallStatus": "no-answer"}
    sig = twilio_signature("tok", "https://leads.example.com/webhooks/twilio/missed-call", params)
    r = client.post("/webhooks/twilio/missed-call", data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200 and "<Response/>" in r.text
    assert "Sorry we missed your call" in notifier.outbox[0]["body"]

    forged = client.post("/webhooks/twilio/missed-call", data=params, headers={"X-Twilio-Signature": "nope"})
    assert forged.status_code == 401


def test_answered_call_is_not_a_lead():
    client, notifier = make_client()
    client.post(
        "/webhooks/twilio/missed-call", data={"CallSid": "CA2", "From": "+15125550142", "CallStatus": "completed"}
    )
    assert client.get("/leads").json() == []
    assert notifier.outbox == []


def test_touch_metrics_and_dashboard():
    client, _ = make_client()
    lead_id = client.post("/webhooks/form", json={"phone": "512-555-0142", "message": "quote"}).json()["lead_id"]
    assert client.post(f"/leads/{lead_id}/touch", json={"by": "dispatch"}).json()["first_touch"] is True
    assert client.post("/leads/999/touch").status_code == 404
    detail = client.get(f"/leads/{lead_id}").json()
    assert detail["human_touched_at"] is not None
    assert [e["kind"] for e in detail["events"]][-1] == "human_touch"
    assert client.get("/metrics").json()["total_leads"] == 1
    page = client.get("/")
    assert page.status_code == 200 and "lead response" in page.text


def test_dashboard_escapes_user_input():
    client, _ = make_client()
    client.post("/webhooks/form", json={"phone": "512-555-0142", "name": "<script>alert(1)</script>"})
    page = client.get("/").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_demo_simulator_endpoint():
    client, _ = make_client()
    r = client.post("/demo/simulate?count=10&reset=true")
    assert r.json()["created"] > 0
    assert client.get("/metrics").json()["total_leads"] == r.json()["created"]


def test_simulator_hidden_outside_demo_mode():
    client, _ = make_client(demo_mode=False, webhook_secret="k")
    assert client.post("/demo/simulate").status_code == 404
