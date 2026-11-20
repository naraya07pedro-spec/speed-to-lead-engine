from datetime import timedelta

from app import simulator
from app.metrics import build_report
from app.service import InboundLead


def test_report_numbers(service, store, clock):
    def add(phone, touch_after=None, source="web_form"):
        r = service.ingest(InboundLead(source=source, phone=phone, message="quote", external_id=phone))
        if touch_after is not None:
            saved = clock.now
            clock.advance(seconds=touch_after)
            service.mark_touched(r.lead_id)
            clock.now = saved
        return r

    add("512-555-0101", touch_after=60)
    add("512-555-0102", touch_after=200)
    add("512-555-0103", touch_after=1200, source="missed_call")
    add("512-555-0104")  # never called back
    clock.advance(minutes=30)

    report = build_report(store.all_since(clock.now - timedelta(days=14)), now=clock.now, window_days=14, sla_minutes=5)
    assert report.total_leads == 4
    assert report.median_human_response_seconds == 200
    assert report.human_within_5_min_pct == 50.0  # 2 of 4 leads
    assert report.not_contacted == 1
    assert report.not_contacted_over_sla == 1
    assert report.by_source["missed_call"].leads == 1
    assert report.by_source["web_form"].contacted == 2


def test_empty_report():
    from datetime import datetime, timezone

    report = build_report([], now=datetime.now(timezone.utc), window_days=14, sla_minutes=5)
    assert report.total_leads == 0
    assert report.median_human_response_seconds is None
    assert report.human_within_5_min_pct is None


def test_simulator_produces_leakage_and_duplicates(service, store):
    created = simulator.run(service, 40, seed=7)
    leads = store.recent(100)
    assert created == len(leads) > 0
    assert sum(lead.duplicate_count for lead in leads) >= 1
    assert any(lead.human_touched_at is None for lead in leads)
    assert any(lead.escalated_at is not None for lead in leads)
