from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.delivery import ConsoleNotifier, DeliveryError, NullCRM
from app.service import LeadService
from app.store import Lead, Store

# Tuesday 10:00 in Austin (CDT, UTC-5): inside business hours.
TUESDAY_10AM_CT = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime = TUESDAY_10AM_CT) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@dataclass
class FlakyNotifier:
    """Fails ``failures`` times, then succeeds."""

    failures: int = 1
    retryable: bool = True
    channel: str = "flaky"
    calls: int = 0
    sent: list[dict[str, str]] = field(default_factory=list)

    def send(self, to: str, body: str) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise DeliveryError("provider down", retryable=self.retryable)
        self.sent.append({"to": to, "body": body})
        return f"sid-{self.calls}"


@dataclass
class RecordingCRM:
    name: str = "recording"
    synced: list[int] = field(default_factory=list)

    def upsert_contact(self, lead: Lead) -> str | None:
        self.synced.append(lead.id)
        return f"crm-{lead.id}"


@pytest.fixture
def settings() -> Settings:
    return Settings(database_path=":memory:", owner_phone="+15125550199", escalation_minutes=5)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def notifier() -> ConsoleNotifier:
    return ConsoleNotifier()


@pytest.fixture
def service(settings, store, notifier, clock) -> LeadService:
    return LeadService(settings, store, notifier, NullCRM(), clock=clock, sleep=lambda _: None)
