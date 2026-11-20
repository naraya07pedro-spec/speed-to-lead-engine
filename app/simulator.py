"""Synthetic traffic for the demo dashboard. All names and numbers are fake
(555-01xx is reserved for fiction)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from .service import InboundLead, LeadService

FIRST = ["Maria", "James", "Linda", "Robert", "Ana", "David", "Karen", "Luis", "Sarah", "Mike", "Rosa", "Kevin"]
LAST = ["Garcia", "Johnson", "Martinez", "Smith", "Lopez", "Brown", "Hernandez", "Davis", "Wilson", "Moore"]
MESSAGES = [
    "AC is blowing warm air and it's 101 outside",
    "No AC since this morning, we have a newborn at home",
    "Water heater leaking in the garage",
    "Pipe burst under the kitchen sink, water everywhere",
    "Need a quote to replace a 15 year old unit",
    "Want to schedule an annual tune-up",
    "Toilet keeps running, not urgent",
    "I think I smell gas near the furnace",
    "Drain backing up in the shower",
    "Thermostat not responding",
]
SOURCES = ["web_form", "web_form", "missed_call", "google_lsa", "facebook"]


def run(service: LeadService, count: int = 25, *, seed: int | None = None, contact_rate: float = 0.7) -> int:
    """Replay ``count`` leads over the past few days with realistic gaps.

    A share of them get a human touch after a random delay; the rest are left
    open so the dashboard shows real leakage.
    """
    rng = random.Random(seed)  # noqa: S311 (fake demo data, not security)
    original_clock = service.clock
    base = service.clock() - timedelta(days=3)
    created = 0
    last_phone: str | None = None
    times = sorted(base + timedelta(minutes=rng.randint(0, 3 * 24 * 60)) for _ in range(count))
    try:
        for i, at in enumerate(times):
            service.clock = lambda at=at: at
            area = rng.choice(["512", "737", "210", "830"])
            phone = f"({area}) 555-01{i % 100:02d}"
            if i % 8 == 7 and last_phone:
                phone = last_phone  # same caller trying again: should be absorbed, not re-texted
            last_phone = phone
            lead = InboundLead(
                source=rng.choice(SOURCES),
                name=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                phone=phone,
                message=rng.choice(MESSAGES),
                external_id=f"sim-{seed}-{i}" if seed is not None else None,
            )
            result = service.ingest(lead)
            if result.status.value != "created" or result.lead_id is None:
                continue
            created += 1
            if rng.random() < contact_rate:
                delay = timedelta(seconds=rng.choice([45, 90, 160, 240, 420, 900, 2400, 7200]))
                touched: datetime = at + delay
                service.clock = lambda touched=touched: touched
                service.mark_touched(result.lead_id, by="simulator")
    finally:
        service.clock = original_clock
    service.escalate_stale()  # leads nobody called back get flagged, as in production
    return created
