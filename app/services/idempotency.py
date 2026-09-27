from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.db import connect, dumps, loads


def find(key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM action_runs WHERE idempotency_key=?", (key,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["request"] = loads(item.pop("request_json"))
    item["result"] = loads(item.pop("result_json"))
    return item


def save(run_id: str, key: str | None, app_id: str, capability: str, status: str,
         request: dict[str, Any], result: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO action_runs VALUES(?,?,?,?,?,?,?,?)",
            (run_id, key, app_id, capability, status, dumps(request), dumps(result), datetime.now(timezone.utc).isoformat()),
        )
