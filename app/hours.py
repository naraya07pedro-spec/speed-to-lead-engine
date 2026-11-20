"""Business-hours logic in the shop's own time zone."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import Settings


def local_now(settings: Settings, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo(settings.business_timezone))


def is_open(settings: Settings, now: datetime | None = None) -> bool:
    local = local_now(settings, now)
    if local.weekday() == 6:  # Sunday: on-call only
        return False
    close = settings.close_hour if local.weekday() < 5 else min(settings.close_hour, 14)
    return settings.open_hour <= local.hour < close
