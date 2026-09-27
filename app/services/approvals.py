from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.db import connect, dumps, loads


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_approval(app_id: str, capability: str, actor: str, request: dict[str, Any]) -> str:
    from app.services.registry import registry
    manifest = registry.get(app_id)
    organization_id = manifest.organization_id if manifest else "global"
    approval_id = str(uuid4())
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO approvals(id,created_at,updated_at,app_id,capability,actor,status,request_json,approved_by,note,organization_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (approval_id, now, now, app_id, capability, actor, "pending", dumps(request), None, None, organization_id),
        )
    return approval_id


def get_approval(approval_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not row:
        return None
    item = dict(row); item["request"] = loads(item.pop("request_json")); return item


def list_approvals(status: str | None = None, organization_id: str | None = None) -> list[dict[str, Any]]:
    where=[]; params=[]
    if status: where.append("status=?"); params.append(status)
    if organization_id is not None: where.append("organization_id=?"); params.append(organization_id)
    sql="SELECT * FROM approvals" + (" WHERE "+" AND ".join(where) if where else "") + " ORDER BY created_at DESC"
    with connect() as conn: rows=conn.execute(sql,tuple(params)).fetchall()
    out=[]
    for row in rows:
        item=dict(row); item["request"]=loads(item.pop("request_json")); out.append(item)
    return out


def resolve_approval(approval_id: str, status: str, approved_by: str, note: str = "") -> bool:
    if status not in {"approved", "rejected"}: raise ValueError("invalid approval status")
    with connect() as conn:
        row=conn.execute("SELECT status FROM approvals WHERE id=?",(approval_id,)).fetchone()
        if not row or row["status"] != "pending": return False
        conn.execute("UPDATE approvals SET status=?,approved_by=?,note=?,updated_at=? WHERE id=?",(status,approved_by,note,_now(),approval_id))
    return True
