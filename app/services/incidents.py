from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.db import connect, dumps, loads


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_incident(*, organization_id: str, title: str, description: str, severity: str = "high",
                  app_id: str | None = None, fingerprint: str | None = None, source_event_id: str | None = None,
                  metadata: dict[str, Any] | None = None) -> dict:
    incident_id = str(uuid4())
    now = _now()
    fp = fingerprint or f"{app_id or 'global'}:{title}"
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM incidents WHERE organization_id=? AND fingerprint=? AND status IN ('open','acknowledged') ORDER BY opened_at DESC LIMIT 1",
            (organization_id, fp),
        ).fetchone()
        if existing:
            conn.execute("UPDATE incidents SET updated_at=?,description=?,metadata_json=? WHERE id=?",
                         (now, description, dumps(metadata or {}), existing["id"]))
            return _decode(existing)
        conn.execute(
            "INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, organization_id, app_id, severity, "open", title, description, fp, source_event_id,
             dumps(metadata or {}), now, now, None),
        )
    incident = get_incident(incident_id) or {"id": incident_id, "organization_id": organization_id}
    try:
        from app.services.jobs import enqueue
        enqueue("notify_incident", {"incident_id": incident_id, "organization_id": organization_id}, max_attempts=3, organization_id=organization_id)
    except Exception:
        pass
    return incident


def _decode(row) -> dict:
    item = dict(row)
    item["metadata"] = loads(item.pop("metadata_json"))
    return item


def get_incident(incident_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    return _decode(row) if row else None


def list_incidents(status: str | None = None, organization_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM incidents WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status=?"; params.append(status)
    if organization_id:
        sql += " AND organization_id=?"; params.append(organization_id)
    sql += " ORDER BY opened_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_decode(r) for r in rows]


def resolve_incident(incident_id: str, note: str) -> bool:
    now = _now()
    with connect() as conn:
        row = conn.execute("SELECT metadata_json,status FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row or row["status"] == "resolved":
            return False
        meta = loads(row["metadata_json"])
        meta["resolution_note"] = note
        result = conn.execute("UPDATE incidents SET status='resolved',metadata_json=?,updated_at=?,resolved_at=? WHERE id=?",
                              (dumps(meta), now, now, incident_id))
    return result.rowcount > 0
