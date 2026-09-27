from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.db import connect, dumps, loads


def _organization_for_app(app_id: str | None) -> str:
    if not app_id:
        return "global"
    from app.services.registry import registry
    manifest = registry.get(app_id)
    return manifest.organization_id if manifest else "global"


def audit(*, actor: str, app_id: str | None, capability: str | None, action: str, decision: str,
          reason: str, request: dict[str, Any] | None = None, result: dict[str, Any] | None = None,
          organization_id: str | None = None) -> str:
    event_id = str(uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    org = organization_id or _organization_for_app(app_id)
    with connect() as conn:
        conn.execute(
            """INSERT INTO audit_events(id,ts,actor,app_id,capability,action,decision,reason,request_json,result_json,organization_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, ts, actor, app_id, capability, action, decision, reason,
             dumps(request or {}), dumps(result or {}), org),
        )
    return event_id


def recent(limit: int = 100, organization_id: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    with connect() as conn:
        if organization_id is None:
            rows = conn.execute("SELECT * FROM audit_events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_events WHERE organization_id=? ORDER BY ts DESC LIMIT ?", (organization_id, limit)).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["request"] = loads(item.pop("request_json"))
        item["result"] = loads(item.pop("result_json"))
        out.append(item)
    return out
