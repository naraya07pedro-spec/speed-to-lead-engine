"""Runtime configuration, read once from environment variables.

Kept dependency-free on purpose: a frozen dataclass is enough for a dozen
settings and keeps the core importable without the web stack.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    business_name: str = "Lone Star Air & Plumbing"
    business_timezone: str = "America/Chicago"
    # Opening hours in local time, 24h clock. Weekend handling lives in hours.py.
    open_hour: int = 7
    close_hour: int = 19
    owner_phone: str | None = None

    database_path: str = "data/leads.db"

    # How long a lead may sit without a human touch before the owner is paged.
    escalation_minutes: int = 5
    # Window in which a second enquiry from the same phone/email is a duplicate.
    dedupe_window_hours: int = 24

    # Notifier: "console" (demo, no cost) or "twilio".
    notifier: str = "console"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None

    # CRM: "none" or "hubspot".
    crm: str = "none"
    hubspot_token: str | None = None

    # Shared secret for HMAC-signed form webhooks. Empty disables the check
    # (only acceptable in demo mode).
    webhook_secret: str = ""
    verify_twilio_signature: bool = False
    public_base_url: str = "http://localhost:8000"

    demo_mode: bool = True
    max_send_attempts: int = 3

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            business_name=os.getenv("BUSINESS_NAME", cls.business_name),
            business_timezone=os.getenv("BUSINESS_TIMEZONE", cls.business_timezone),
            open_hour=_env_int("OPEN_HOUR", cls.open_hour),
            close_hour=_env_int("CLOSE_HOUR", cls.close_hour),
            owner_phone=os.getenv("OWNER_PHONE") or None,
            database_path=os.getenv("DATABASE_PATH", cls.database_path),
            escalation_minutes=_env_int("ESCALATION_MINUTES", cls.escalation_minutes),
            dedupe_window_hours=_env_int("DEDUPE_WINDOW_HOURS", cls.dedupe_window_hours),
            notifier=os.getenv("NOTIFIER", cls.notifier),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID") or None,
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN") or None,
            twilio_from_number=os.getenv("TWILIO_FROM_NUMBER") or None,
            crm=os.getenv("CRM", cls.crm),
            hubspot_token=os.getenv("HUBSPOT_TOKEN") or None,
            webhook_secret=os.getenv("WEBHOOK_SECRET", cls.webhook_secret),
            verify_twilio_signature=_env_bool("VERIFY_TWILIO_SIGNATURE", False),
            public_base_url=os.getenv("PUBLIC_BASE_URL", cls.public_base_url),
            demo_mode=_env_bool("DEMO_MODE", True),
            max_send_attempts=_env_int("MAX_SEND_ATTEMPTS", cls.max_send_attempts),
        )
