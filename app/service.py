"""The intake pipeline.

    webhook -> normalize -> idempotency check -> dedupe -> persist
            -> classify -> auto-reply (retry, dead-letter) -> owner alert
            -> CRM sync (retry, dead-letter)

Persisting happens before any outbound call, so a Twilio or CRM outage can
delay a reply but can never lose a lead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from . import templates
from .config import Settings
from .delivery import CRM, DeliveryError, Notifier, with_retries
from .hours import is_open
from .normalize import clean_text, idempotency_key, normalize_email, normalize_phone
from .store import Lead, Store, utcnow, window_start
from .urgency import Urgency, classify


class IntakeStatus(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"  # same person again inside the dedupe window
    REPLAY = "replay"  # exact same webhook delivered twice
    REJECTED = "rejected"  # no way to contact the person


@dataclass
class InboundLead:
    source: str
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    message: str | None = None
    external_id: str | None = None


@dataclass
class IntakeResult:
    status: IntakeStatus
    lead_id: int | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "lead_id": self.lead_id, "reason": self.reason}


class LeadService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        notifier: Notifier,
        crm: CRM,
        *,
        llm: Callable[[str], Urgency] | None = None,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.notifier = notifier
        self.crm = crm
        self.llm = llm
        self.clock = clock
        self._sleep = sleep

    # --- intake ----------------------------------------------------------

    def ingest(self, inbound: InboundLead) -> IntakeResult:
        now = self.clock()
        phone = normalize_phone(inbound.phone)
        email = normalize_email(inbound.email)
        name = clean_text(inbound.name, 120) or None
        message = clean_text(inbound.message)
        key = idempotency_key(inbound.source, inbound.external_id, phone, email, message)

        if not phone and not email:
            self.store.log(None, "rejected", now, source=inbound.source, reason="no valid phone or email")
            return IntakeResult(IntakeStatus.REJECTED, None, "no valid phone or email")

        with self.store.transaction() as conn:
            existing = self.store.lead_for_key(conn, key)
            if existing is not None:
                return IntakeResult(IntakeStatus.REPLAY, existing, "webhook already processed")

            match = self.store.find_recent_match(
                conn, phone=phone, email=email, since=window_start(now, self.settings.dedupe_window_hours)
            )
            if match is not None:
                self.store.bump_duplicate(conn, match.id)
                self.store.remember_key(conn, key, match.id, now)
                duplicate_of = match.id
            else:
                duplicate_of = None
                result = classify(message, self.llm)
                lead_id = self.store.insert_lead(
                    conn,
                    source=inbound.source,
                    name=name,
                    phone=phone,
                    email=email,
                    message=message,
                    urgency=result.urgency.value,
                    urgency_reason=result.reason,
                    received_at=now,
                )
                self.store.remember_key(conn, key, lead_id, now)

        if duplicate_of is not None:
            # Do not text the customer again; a human already has this thread.
            self.store.log(duplicate_of, "duplicate_enquiry", now, source=inbound.source, message=message)
            return IntakeResult(IntakeStatus.DUPLICATE, duplicate_of, "existing lead inside dedupe window")

        self.store.log(
            lead_id, "received", now, source=inbound.source, urgency=result.urgency.value, classifier=result.source
        )
        lead = self.store.get(lead_id)
        assert lead is not None
        self._auto_reply(lead, now)
        self._alert_owner(lead)
        self._sync_crm(lead)
        return IntakeResult(IntakeStatus.CREATED, lead_id)

    # --- outbound steps --------------------------------------------------

    def _deliver(self, lead_id: int, channel: str, payload: dict[str, Any], fn: Callable[[], Any]) -> Any | None:
        kwargs: dict[str, Any] = {"attempts": self.settings.max_send_attempts}
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        try:
            value, used = with_retries(fn, **kwargs)
        except DeliveryError as exc:
            attempts = getattr(exc, "attempts", self.settings.max_send_attempts)
            self.store.dead_letter(lead_id, channel, payload, str(exc), attempts)
            self.store.log(lead_id, f"{channel}_failed", error=str(exc), attempts=attempts)
            return None
        self.store.log(lead_id, f"{channel}_ok", attempts=used)
        return value

    def _auto_reply(self, lead: Lead, now: datetime) -> None:
        if not lead.phone:
            self.store.log(lead.id, "auto_reply_skipped", reason="email-only lead")
            return
        body = templates.customer_reply(
            name=lead.name,
            business=self.settings.business_name,
            urgency=Urgency(lead.urgency),
            open_now=is_open(self.settings, now),
            source=lead.source,
        )
        sid = self._deliver(
            lead.id, "auto_reply", {"to": lead.phone, "body": body}, lambda: self.notifier.send(lead.phone, body)
        )  # type: ignore[arg-type]
        if sid is not None:
            self.store.set_timestamp(lead.id, "auto_replied_at", self.clock())

    def _alert_owner(self, lead: Lead) -> None:
        if not self.settings.owner_phone:
            return
        if Urgency(lead.urgency) not in (Urgency.SAFETY, Urgency.EMERGENCY):
            return
        body = templates.owner_alert(lead_id=lead.id, name=lead.name, phone=lead.phone, urgency=Urgency(lead.urgency))
        self._deliver(
            lead.id,
            "owner_alert",
            {"to": self.settings.owner_phone, "body": body},
            lambda: self.notifier.send(self.settings.owner_phone, body),
        )  # type: ignore[arg-type]

    def _sync_crm(self, lead: Lead) -> None:
        if self.crm.name == "none":
            return
        crm_id = self._deliver(lead.id, "crm_sync", {"lead_id": lead.id}, lambda: self.crm.upsert_contact(lead))
        if crm_id:
            self.store.set_crm_id(lead.id, crm_id)

    # --- lifecycle -------------------------------------------------------

    def mark_touched(self, lead_id: int, by: str = "staff") -> bool:
        """Record the first human contact. Later calls are no-ops."""
        if self.store.get(lead_id) is None:
            raise KeyError(lead_id)
        first = self.store.set_timestamp(lead_id, "human_touched_at", self.clock())
        self.store.log(lead_id, "human_touch", by=by, first=first)
        return first

    def escalate_stale(self) -> list[int]:
        """Page the owner about leads nobody has called back yet."""
        now = self.clock()
        cutoff = now - timedelta(minutes=self.settings.escalation_minutes)
        stale = self.store.claim_for_escalation(cutoff, now)
        for lead in stale:
            waited = int((now - lead.received_at).total_seconds() // 60)
            self.store.log(lead.id, "escalated", minutes_waiting=waited)
            if self.settings.owner_phone:
                body = templates.owner_alert(
                    lead_id=lead.id,
                    name=lead.name,
                    phone=lead.phone,
                    urgency=Urgency(lead.urgency),
                    minutes_waiting=waited,
                )
                self._deliver(
                    lead.id,
                    "escalation",
                    {"to": self.settings.owner_phone, "body": body},
                    lambda b=body: self.notifier.send(self.settings.owner_phone, b),
                )  # type: ignore[arg-type]
        return [lead.id for lead in stale]
