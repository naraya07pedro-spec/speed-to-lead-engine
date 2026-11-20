"""Outbound channels: SMS notifier and CRM sync.

Both talk HTTP through a tiny injectable ``Transport`` so tests never touch
the network and the core has no third-party HTTP dependency.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Settings
from .store import Lead


class DeliveryError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class HttpResponse:
    status: int
    body: str

    def json(self) -> Any:
        return json.loads(self.body or "null")


Transport = Callable[[str, str, dict[str, str], bytes | None], HttpResponse]


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> HttpResponse:
    if not url.startswith("https://"):
        raise DeliveryError(f"refusing non-https url: {url}", retryable=False)
    req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 (https enforced above)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return HttpResponse(resp.status, resp.read().decode())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read().decode(errors="replace"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DeliveryError(f"network error: {exc}") from exc


def raise_for_status(resp: HttpResponse, what: str) -> None:
    if resp.status < 300:
        return
    # 4xx (except 429) means our request is wrong; retrying will not help.
    retryable = resp.status == 429 or resp.status >= 500
    raise DeliveryError(f"{what} failed with HTTP {resp.status}: {resp.body[:200]}", retryable=retryable)


def with_retries(
    fn: Callable[[], Any],
    *,
    attempts: int,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Any, int]:
    """Run ``fn`` with exponential backoff. Returns (result, attempts_used)."""
    last: DeliveryError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(), attempt
        except DeliveryError as exc:
            last = exc
            if not exc.retryable or attempt == attempts:
                break
            sleep(base_delay * 2 ** (attempt - 1))
    assert last is not None
    last.attempts = attempt  # type: ignore[attr-defined]
    raise last


# --- SMS ------------------------------------------------------------------


class Notifier(Protocol):
    channel: str

    def send(self, to: str, body: str) -> str: ...


@dataclass
class ConsoleNotifier:
    """Demo notifier: records messages instead of sending them."""

    channel: str = "console"
    outbox: list[dict[str, str]] = field(default_factory=list)

    def send(self, to: str, body: str) -> str:
        self.outbox.append({"to": to, "body": body})
        return f"console-{len(self.outbox)}"


@dataclass
class TwilioNotifier:
    account_sid: str
    auth_token: str
    from_number: str
    transport: Transport = urllib_transport
    channel: str = "twilio_sms"

    def send(self, to: str, body: str) -> str:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        creds = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        data = urllib.parse.urlencode({"To": to, "From": self.from_number, "Body": body}).encode()
        resp = self.transport(
            "POST",
            url,
            {"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
            data,
        )
        raise_for_status(resp, "twilio send")
        return str(resp.json().get("sid", ""))


# --- CRM ------------------------------------------------------------------


class CRM(Protocol):
    name: str

    def upsert_contact(self, lead: Lead) -> str | None: ...


@dataclass
class NullCRM:
    name: str = "none"

    def upsert_contact(self, lead: Lead) -> str | None:
        return None


@dataclass
class HubSpotCRM:
    """Creates a contact, or updates it when HubSpot reports a conflict."""

    token: str
    transport: Transport = urllib_transport
    name: str = "hubspot"
    base_url: str = "https://api.hubapi.com"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def upsert_contact(self, lead: Lead) -> str | None:
        first, _, last = (lead.name or "").partition(" ")
        props = {
            k: v
            for k, v in {
                "firstname": first or None,
                "lastname": last or None,
                "phone": lead.phone,
                "email": lead.email,
                "hs_lead_status": "NEW",
                "message": f"[{lead.urgency}] via {lead.source}: {lead.message}"[:1000],
            }.items()
            if v
        }
        body = json.dumps({"properties": props}).encode()
        resp = self.transport("POST", f"{self.base_url}/crm/v3/objects/contacts", self._headers(), body)
        if resp.status == 409 and lead.email:
            # Contact exists: update it by email instead of failing the sync.
            url = f"{self.base_url}/crm/v3/objects/contacts/{urllib.parse.quote(lead.email)}?idProperty=email"
            resp = self.transport("PATCH", url, self._headers(), body)
        raise_for_status(resp, "hubspot upsert")
        return str(resp.json().get("id")) if resp.body else None


def build_notifier(settings: Settings) -> Notifier:
    if settings.notifier == "twilio":
        missing = [
            n
            for n, v in {
                "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
                "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
                "TWILIO_FROM_NUMBER": settings.twilio_from_number,
            }.items()
            if not v
        ]
        if missing:
            raise RuntimeError(f"NOTIFIER=twilio but missing: {', '.join(missing)}")
        return TwilioNotifier(settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from_number)  # type: ignore[arg-type]
    return ConsoleNotifier()


def build_crm(settings: Settings) -> CRM:
    if settings.crm == "hubspot":
        if not settings.hubspot_token:
            raise RuntimeError("CRM=hubspot but HUBSPOT_TOKEN is not set")
        return HubSpotCRM(settings.hubspot_token)
    return NullCRM()
