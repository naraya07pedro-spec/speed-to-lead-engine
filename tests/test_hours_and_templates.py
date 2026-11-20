from datetime import datetime, timezone

from app.config import Settings
from app.hours import is_open
from app.templates import customer_reply, first_name
from app.urgency import Urgency

S = Settings()


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_weekday_hours_in_central_time():
    assert is_open(S, utc(2026, 6, 9, 15))  # Tue 10:00 CDT
    assert not is_open(S, utc(2026, 6, 9, 11))  # Tue 06:00 CDT
    assert not is_open(S, utc(2026, 6, 10, 0, 30))  # Tue 19:30 CDT


def test_dst_is_respected():
    # 13:00 UTC is 07:00 CST in January (UTC-6) but 08:00 CDT in June.
    assert is_open(S, utc(2026, 1, 13, 13))
    assert not is_open(S, utc(2026, 1, 13, 12, 30))


def test_saturday_closes_early_and_sunday_closed():
    assert is_open(S, utc(2026, 6, 13, 16))  # Sat 11:00
    assert not is_open(S, utc(2026, 6, 13, 20))  # Sat 15:00
    assert not is_open(S, utc(2026, 6, 14, 16))  # Sun 11:00


def test_first_name():
    assert first_name("maria garcia") == "Maria"
    assert first_name(None) == "there"


def test_safety_reply_tells_customer_to_leave():
    text = customer_reply(name="Ana", business="Acme", urgency=Urgency.SAFETY, open_now=True, source="web_form")
    assert "leave the house" in text and "911" in text


def test_missed_call_reply_apologises():
    text = customer_reply(name=None, business="Acme", urgency=Urgency.STANDARD, open_now=True, source="missed_call")
    assert text.startswith("Hi there, this is Acme. Sorry we missed your call.")


def test_after_hours_reply_sets_expectation():
    text = customer_reply(name="Bo", business="Acme", urgency=Urgency.STANDARD, open_now=False, source="web_form")
    assert "closed right now" in text


def test_sms_length_stays_reasonable():
    for urgency in Urgency:
        for open_now in (True, False):
            text = customer_reply(
                name="Christopher",
                business="Lone Star Air & Plumbing",
                urgency=urgency,
                open_now=open_now,
                source="missed_call",
            )
            assert len(text) <= 320  # at most two SMS segments
