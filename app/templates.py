"""Outbound message copy. Short, plain, and honest about what happens next."""

from __future__ import annotations

from .urgency import Urgency


def first_name(name: str | None) -> str:
    if not name:
        return "there"
    return name.strip().split()[0].title()


def customer_reply(*, name: str | None, business: str, urgency: Urgency, open_now: bool, source: str) -> str:
    hi = f"Hi {first_name(name)}, this is {business}."
    if urgency is Urgency.SAFETY:
        return (
            f"{hi} If you smell gas or a CO alarm is going off, leave the house now "
            "and call 911 or your gas utility from outside. We are paging our on-call tech as well."
        )
    if source == "missed_call":
        opener = f"{hi} Sorry we missed your call."
    else:
        opener = f"{hi} Thanks for reaching out."
    if urgency is Urgency.EMERGENCY:
        return f"{opener} We flagged this as urgent and a tech will call you within minutes. Reply here with your address to save time."
    if open_now:
        return f"{opener} Someone from our team will call you shortly. What is the best time to reach you?"
    return (
        f"{opener} We are closed right now. Reply with what is going on and we will call you first thing when we open."
    )


def owner_alert(
    *, lead_id: int, name: str | None, phone: str | None, urgency: Urgency, minutes_waiting: int | None = None
) -> str:
    who = name or phone or f"lead #{lead_id}"
    if minutes_waiting is None:
        return f"[{urgency.value.upper()}] New lead #{lead_id}: {who} {phone or ''}".strip()
    return f"[NO RESPONSE {minutes_waiting}m] Lead #{lead_id} ({who}, {urgency.value}) has not been contacted yet."
