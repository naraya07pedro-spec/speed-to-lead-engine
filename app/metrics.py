"""Lead-leakage metrics: the numbers a 14-day pilot is judged on."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from statistics import median

from .store import Lead


@dataclass
class SourceStats:
    leads: int = 0
    contacted: int = 0
    median_human_seconds: float | None = None


@dataclass
class Report:
    window_days: int
    total_leads: int
    duplicates_absorbed: int
    auto_replied: int
    median_auto_reply_seconds: float | None
    median_human_response_seconds: float | None
    human_within_5_min_pct: float | None
    not_contacted: int
    not_contacted_over_sla: int
    escalated: int
    by_source: dict[str, SourceStats] = field(default_factory=dict)
    by_urgency: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _seconds(start: datetime, end: datetime | None) -> float | None:
    return None if end is None else max(0.0, (end - start).total_seconds())


def _median(values: list[float]) -> float | None:
    return round(median(values), 1) if values else None


def build_report(leads: list[Lead], *, now: datetime, window_days: int, sla_minutes: int) -> Report:
    auto = [s for lead in leads if (s := _seconds(lead.received_at, lead.auto_replied_at)) is not None]
    human = [s for lead in leads if (s := _seconds(lead.received_at, lead.human_touched_at)) is not None]
    sla = timedelta(minutes=sla_minutes)
    uncontacted = [lead for lead in leads if lead.human_touched_at is None]

    by_source: dict[str, SourceStats] = {}
    for src in sorted({lead.source for lead in leads}):
        group = [lead for lead in leads if lead.source == src]
        times = [s for lead in group if (s := _seconds(lead.received_at, lead.human_touched_at)) is not None]
        by_source[src] = SourceStats(len(group), len(times), _median(times))

    by_urgency: dict[str, int] = {}
    for lead in leads:
        by_urgency[lead.urgency] = by_urgency.get(lead.urgency, 0) + 1

    return Report(
        window_days=window_days,
        total_leads=len(leads),
        duplicates_absorbed=sum(lead.duplicate_count for lead in leads),
        auto_replied=len(auto),
        median_auto_reply_seconds=_median(auto),
        median_human_response_seconds=_median(human),
        human_within_5_min_pct=(round(100 * sum(1 for s in human if s <= 300) / len(leads), 1) if leads else None),
        not_contacted=len(uncontacted),
        not_contacted_over_sla=sum(1 for lead in uncontacted if now - lead.received_at > sla),
        escalated=sum(1 for lead in leads if lead.escalated_at is not None),
        by_source=by_source,
        by_urgency=by_urgency,
    )
