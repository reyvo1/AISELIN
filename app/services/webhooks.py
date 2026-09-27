from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import settings
from app.core.db import connect
from app.services.vault import get_secret


def verify_signed_event(app_id: str, body: bytes, timestamp: str | None, signature: str | None) -> tuple[bool, str]:
    if not timestamp or not signature:
        return False, "missing webhook signature headers"
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "invalid webhook timestamp"
    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - ts) > settings.webhook_tolerance_seconds:
        return False, "webhook timestamp outside allowed tolerance"
    secret = get_secret(f"webhook:{app_id}", "event")
    if not secret:
        return False, "webhook secret is not configured"
    signed = timestamp.encode() + b"." + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    normalized = signature.removeprefix("sha256=")
    if not hmac.compare_digest(expected, normalized):
        return False, "webhook signature mismatch"
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO webhook_receipts VALUES(?,?,?,?,?)",
                (str(uuid4()), app_id, normalized, timestamp, datetime.now(timezone.utc).isoformat()),
            )
    except Exception:
        return False, "webhook replay detected"
    return True, "ok"
