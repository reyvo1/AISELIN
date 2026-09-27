from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import settings
from app.core.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def allow(app_id: str, capability: str) -> tuple[bool, str]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM circuit_breakers WHERE app_id=? AND capability=?", (app_id, capability)).fetchone()
    if not row or row["state"] == "closed":
        return True, "closed"
    if row["state"] == "open" and row["opened_at"]:
        opened = datetime.fromisoformat(row["opened_at"])
        if (datetime.now(timezone.utc) - opened).total_seconds() >= int(row["reset_after_seconds"]):
            with connect() as conn:
                conn.execute("UPDATE circuit_breakers SET state='half_open',updated_at=? WHERE app_id=? AND capability=?", (_now(), app_id, capability))
            return True, "half_open"
        return False, "open"
    return True, row["state"]


def record_success(app_id: str, capability: str) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO circuit_breakers(app_id,capability,state,failure_count,threshold,opened_at,reset_after_seconds,updated_at)
               VALUES(?,?,'closed',0,?,NULL,?,?) ON CONFLICT(app_id,capability) DO UPDATE SET
               state='closed',failure_count=0,opened_at=NULL,updated_at=excluded.updated_at""",
            (app_id, capability, settings.circuit_failure_threshold, settings.circuit_reset_seconds, now),
        )


def record_failure(app_id: str, capability: str, threshold: int | None = None, reset_seconds: int | None = None) -> dict:
    threshold = threshold or settings.circuit_failure_threshold
    reset_seconds = reset_seconds or settings.circuit_reset_seconds
    now = _now()
    with connect() as conn:
        row = conn.execute("SELECT failure_count FROM circuit_breakers WHERE app_id=? AND capability=?", (app_id, capability)).fetchone()
        failures = (int(row["failure_count"]) if row else 0) + 1
        state = "open" if failures >= threshold else "closed"
        opened = now if state == "open" else None
        conn.execute(
            """INSERT INTO circuit_breakers(app_id,capability,state,failure_count,threshold,opened_at,reset_after_seconds,updated_at)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(app_id,capability) DO UPDATE SET state=excluded.state,
               failure_count=excluded.failure_count,threshold=excluded.threshold,opened_at=excluded.opened_at,
               reset_after_seconds=excluded.reset_after_seconds,updated_at=excluded.updated_at""",
            (app_id, capability, state, failures, threshold, opened, reset_seconds, now),
        )
    return {"state": state, "failure_count": failures, "threshold": threshold}
