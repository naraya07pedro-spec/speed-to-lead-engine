from app.delivery import ConsoleNotifier, NullCRM
from app.service import InboundLead, IntakeStatus, LeadService

from .conftest import FlakyNotifier, RecordingCRM


def lead(**kw):
    base = {"source": "web_form", "name": "Maria Garcia", "phone": "(512) 555-0142", "message": "No AC since noon"}
    base.update(kw)
    return InboundLead(**base)


def test_new_lead_is_stored_classified_and_answered(service, notifier, store):
    result = service.ingest(lead())
    assert result.status is IntakeStatus.CREATED

    saved = store.get(result.lead_id)
    assert saved.phone == "+15125550142"
    assert saved.urgency == "emergency"
    assert saved.auto_replied_at is not None

    to_customer = [m for m in notifier.outbox if m["to"] == "+15125550142"]
    assert len(to_customer) == 1
    assert to_customer[0]["body"].startswith("Hi Maria")


def test_emergency_pages_owner_immediately(service, notifier, settings):
    service.ingest(lead())
    assert any(m["to"] == settings.owner_phone and "EMERGENCY" in m["body"] for m in notifier.outbox)


def test_standard_lead_does_not_page_owner(service, notifier, settings):
    service.ingest(lead(message="Need a quote for a new water heater"))
    assert not any(m["to"] == settings.owner_phone for m in notifier.outbox)


def test_exact_webhook_replay_is_a_noop(service, notifier):
    first = service.ingest(lead(external_id="sub-1"))
    sent = len(notifier.outbox)
    again = service.ingest(lead(external_id="sub-1"))
    assert again.status is IntakeStatus.REPLAY
    assert again.lead_id == first.lead_id
    assert len(notifier.outbox) == sent


def test_same_person_again_is_absorbed_not_retexted(service, notifier, store, clock):
    first = service.ingest(lead(external_id="a"))
    clock.advance(minutes=20)
    second = service.ingest(lead(external_id="b", source="missed_call", phone="512-555-0142"))
    assert second.status is IntakeStatus.DUPLICATE
    assert second.lead_id == first.lead_id
    assert store.get(first.lead_id).duplicate_count == 1
    assert sum(1 for m in notifier.outbox if m["to"] == "+15125550142") == 1


def test_same_person_after_window_is_a_new_lead(service, clock):
    first = service.ingest(lead(external_id="a"))
    clock.advance(hours=25)
    second = service.ingest(lead(external_id="b"))
    assert second.status is IntakeStatus.CREATED
    assert second.lead_id != first.lead_id


def test_lead_without_contact_details_is_rejected(service, store):
    result = service.ingest(lead(phone="call me", email=None))
    assert result.status is IntakeStatus.REJECTED
    assert store.recent() == []


def test_email_only_lead_is_kept_without_sms(service, notifier, store):
    result = service.ingest(lead(phone=None, email="ana@example.com", message="quote please"))
    assert result.status is IntakeStatus.CREATED
    assert store.get(result.lead_id).auto_replied_at is None
    assert notifier.outbox == []


def test_transient_sms_failure_is_retried(settings, store, clock):
    flaky = FlakyNotifier(failures=2)
    svc = LeadService(settings, store, flaky, NullCRM(), clock=clock, sleep=lambda _: None)
    result = svc.ingest(lead(message="quote please"))
    assert store.get(result.lead_id).auto_replied_at is not None
    assert flaky.calls == 3
    assert store.dead_letters() == []


def test_permanent_sms_failure_goes_to_dead_letter_and_lead_survives(settings, store, clock):
    flaky = FlakyNotifier(failures=99, retryable=False)
    svc = LeadService(settings, store, flaky, NullCRM(), clock=clock, sleep=lambda _: None)
    result = svc.ingest(lead(message="quote please"))
    saved = store.get(result.lead_id)
    assert saved is not None and saved.auto_replied_at is None
    assert flaky.calls == 1  # non-retryable: no pointless retries
    letters = store.dead_letters()
    assert len(letters) == 1 and letters[0]["channel"] == "auto_reply"


def test_crm_id_is_saved(settings, store, clock):
    crm = RecordingCRM()
    svc = LeadService(settings, store, ConsoleNotifier(), crm, clock=clock, sleep=lambda _: None)
    result = svc.ingest(lead())
    assert crm.synced == [result.lead_id]
    assert store.get(result.lead_id).crm_id == f"crm-{result.lead_id}"


def test_first_touch_is_recorded_once(service, store, clock):
    result = service.ingest(lead())
    clock.advance(minutes=3)
    assert service.mark_touched(result.lead_id) is True
    touched_at = store.get(result.lead_id).human_touched_at
    clock.advance(minutes=10)
    assert service.mark_touched(result.lead_id) is False
    assert store.get(result.lead_id).human_touched_at == touched_at


def test_escalation_only_hits_stale_untouched_leads_once(service, notifier, settings, clock):
    stale = service.ingest(lead(external_id="1", phone="512-555-0101", message="quote"))
    handled = service.ingest(lead(external_id="2", phone="512-555-0102", message="quote"))
    service.mark_touched(handled.lead_id)
    clock.advance(minutes=4)
    fresh = service.ingest(lead(external_id="3", phone="512-555-0103", message="quote"))
    clock.advance(minutes=2)

    assert service.escalate_stale() == [stale.lead_id]
    assert service.escalate_stale() == []  # idempotent
    pages = [m for m in notifier.outbox if m["to"] == settings.owner_phone and "NO RESPONSE" in m["body"]]
    assert len(pages) == 1
    assert fresh.lead_id not in [stale.lead_id]


def test_audit_trail(service, store):
    result = service.ingest(lead())
    kinds = [e["kind"] for e in store.events(result.lead_id)]
    assert kinds[:2] == ["received", "auto_reply_ok"]
