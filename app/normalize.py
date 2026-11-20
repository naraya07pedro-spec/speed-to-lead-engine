"""Turn messy inbound contact data into keys we can dedupe on.

Phone handling targets North American numbers (the engine's first market is
Texas home services). Anything else that already looks like E.164 is kept.
"""

from __future__ import annotations

import hashlib
import re

_DIGITS = re.compile(r"\D+")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(raw: str | None) -> str | None:
    """Return an E.164 string such as ``+15125550142`` or ``None``.

    >>> normalize_phone("(512) 555-0142")
    '+15125550142'
    >>> normalize_phone("1-512-555-0142")
    '+15125550142'
    >>> normalize_phone("555-0142") is None
    True
    """
    if not raw:
        return None
    raw = raw.strip()
    digits = _DIGITS.sub("", raw)

    if raw.startswith("+") and not digits.startswith("1"):
        # International number already in E.164 form; trust the length rule.
        return f"+{digits}" if 8 <= len(digits) <= 15 else None

    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    # NANP: area code and exchange cannot start with 0 or 1.
    if digits[0] in "01" or digits[3] in "01":
        return None
    return f"+1{digits}"


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    email = raw.strip().lower()
    return email if _EMAIL.match(email) else None


def clean_text(raw: str | None, limit: int = 2000) -> str:
    if not raw:
        return ""
    return " ".join(raw.split())[:limit]


def idempotency_key(source: str, external_id: str | None, *parts: str | None) -> str:
    """Stable key for a single inbound event.

    Providers that give us an id (Twilio CallSid, form submission id) use it.
    Otherwise the normalized payload is hashed, so an exact replay of the same
    webhook is still recognised.
    """
    if external_id:
        material = f"{source}|id|{external_id}"
    else:
        material = f"{source}|payload|" + "|".join(p or "" for p in parts)
    return hashlib.sha256(material.encode()).hexdigest()
