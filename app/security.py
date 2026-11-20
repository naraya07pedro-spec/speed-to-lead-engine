"""Webhook authenticity checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


def sign_body(secret: str, body: bytes, timestamp: str) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify_form_signature(
    secret: str,
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> bool:
    """HMAC-SHA256 over ``"{timestamp}.{body}"`` with a replay window.

    Same scheme Stripe and Slack use: the timestamp is signed so an attacker
    cannot replay an old, validly signed request.
    """
    if not signature or not timestamp:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance_seconds:
        return False
    return hmac.compare_digest(sign_body(secret, body, timestamp), signature)


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's documented scheme: HMAC-SHA1 of URL + sorted key/value pairs, base64."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio(auth_token: str, url: str, params: dict[str, str], signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(twilio_signature(auth_token, url, params), signature)
