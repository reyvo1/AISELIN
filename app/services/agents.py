from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.db import connect, dumps, loads
from app.models import AgentHeartbeat, AgentNodeCreate


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now_dt()).isoformat()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _decode_agent(row) -> dict[str, Any]:
    item = dict(row)
    item["capabilities"] = loads(item.pop("capabilities_json"))
    item["labels"] = loads(item.pop("labels_json"))
    item["metadata"] = loads(item.pop("metadata_json"))
    item.pop("token_hash", None)
    return item


def create_agent(item: AgentNodeCreate) -> dict[str, Any]:
    token = "aioc_agent_" + secrets.token_urlsafe(32)
    now = _iso()
    with connect() as conn:
        org = conn.execute("SELECT id FROM organizations WHERE id=? AND enabled=1", (item.organization_id,)).fetchone()
        if not org:
            raise ValueError("organization not found")
        existing = conn.execute("SELECT id FROM agent_nodes WHERE id=?", (item.id,)).fetchone()
        if existing:
            raise ValueError("agent id already exists")
        conn.execute(
            """INSERT INTO agent_nodes(id,organization_id,name,kind,token_hash,status,capabilities_json,labels_json,metadata_json,version,last_heartbeat,created_at,updated_at)
               VALUES(?,?,?,?,?,'offline',?,?,?,?,NULL,?,?)""",
            (item.id, item.organization_id, item.name, item.kind, _hash(token), dumps(item.capabilities), dumps(item.labels), dumps(item.metadata), "unknown", now, now),
        )
    result = get_agent(item.id) or {"id": item.id}
    result["token"] = token
    return result


def rotate_agent_token(agent_id: str) -> dict[str, Any] | None:
    token = "aioc_agent_" + secrets.token_urlsafe(32)
    with connect() as conn:
        updated = conn.execute("UPDATE agent_nodes SET token_hash=?,updated_at=? WHERE id=?", (_hash(token), _iso(), agent_id))
    if updated.rowcount != 1:
        return None
    result = get_agent(agent_id) or {"id": agent_id}
    result["token"] = token
    return result


def get_agent(agent_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_nodes WHERE id=?", (agent_id,)).fetchone()
    return _decode_agent(row) if row else None


def list_agents(organization_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if organization_id is None:
            rows = conn.execute("SELECT * FROM agent_nodes ORDER BY organization_id,name").fetchall()
        else:
            rows = conn.execute("SELECT * FROM agent_nodes WHERE organization_id=? ORDER BY name", (organization_id,)).fetchall()
    now = _now_dt()
    out = []
    for row in rows:
        item = _decode_agent(row)
        heartbeat = item.get("last_heartbeat")
        if heartbeat:
            try:
                age = (now - datetime.fromisoformat(heartbeat)).total_seconds()
                if age > settings.agent_stale_seconds and item["status"] in {"online", "busy"}:
                    item["effective_status"] = "stale"
                else:
                    item["effective_status"] = item["status"]
            except ValueError:
                item["effective_status"] = "stale"
        else:
            item["effective_status"] = "offline"
        out.append(item)
    return out


def heartbeat(agent_id: str, item: AgentHeartbeat) -> dict[str, Any]:
    now = _iso()
    with connect() as conn:
        row = conn.execute("SELECT capabilities_json,metadata_json FROM agent_nodes WHERE id=?", (agent_id,)).fetchone()
        if not row:
            raise ValueError("agent not found")
        # The registered allow-list is authoritative. The heartbeat may report a subset only.
        registered = set(loads(row["capabilities_json"]))
        reported = set(item.capabilities) if item.capabilities else registered
        unsupported = reported - registered
        if unsupported:
            raise ValueError(f"agent reported undeclared capabilities: {sorted(unsupported)}")
        # Registration metadata is policy/configuration. Heartbeat data is runtime evidence and
        # must never be allowed to overwrite grants such as allowed_secrets.
        metadata = dict(loads(row["metadata_json"]) or {})
        metadata["runtime"] = dict(item.metadata or {})
        conn.execute(
            "UPDATE agent_nodes SET status=?,version=?,last_heartbeat=?,metadata_json=?,updated_at=? WHERE id=?",
            (item.status, item.version, now, dumps(metadata), now, agent_id),
        )
    return get_agent(agent_id) or {"id": agent_id}


def enqueue_agent_task(
    agent_id: str,
    capability: str,
    payload: dict[str, Any],
    *,
    organization_id: str | None = None,
    correlation_type: str | None = None,
    correlation_id: str | None = None,
    max_attempts: int | None = None,
    delay_seconds: int = 0,
) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError("agent not found")
    if organization_id is not None and agent["organization_id"] != organization_id:
        raise ValueError("agent organization mismatch")
    if capability not in agent["capabilities"]:
        raise ValueError(f"agent capability is not allowed: {capability}")
    now = _now_dt()
    task_id = str(uuid4())
    with connect() as conn:
        conn.execute(
            """INSERT INTO agent_tasks(id,organization_id,agent_id,capability,payload_json,status,attempts,max_attempts,available_at,lease_until,result_json,error,correlation_type,correlation_id,artifact_id,created_at,updated_at)
               VALUES(?,?,?,?,?,'queued',0,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, agent["organization_id"], agent_id, capability, dumps(payload),
                max_attempts or settings.agent_max_task_attempts,
                _iso(now + timedelta(seconds=max(0, delay_seconds))), None, dumps({}), None,
                correlation_type, correlation_id, None, _iso(now), _iso(now),
            ),
        )
    return get_agent_task(task_id) or {"id": task_id}


def _decode_task(row) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = loads(item.pop("payload_json"))
    item["result"] = loads(item.pop("result_json"))
    return item


def get_agent_task(task_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    return _decode_task(row) if row else None


def list_agent_tasks(organization_id: str | None = None, agent_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if organization_id is not None:
        where.append("organization_id=?"); params.append(organization_id)
    if agent_id is not None:
        where.append("agent_id=?"); params.append(agent_id)
    if status is not None:
        where.append("status=?"); params.append(status)
    sql = "SELECT * FROM agent_tasks" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_decode_task(row) for row in rows]


def claim_agent_task(agent_id: str) -> dict[str, Any] | None:
    now = _now_dt(); lease_until = now + timedelta(seconds=settings.agent_task_lease_seconds)
    with connect() as conn:
        # Recover abandoned tasks for this agent only.
        conn.execute(
            "UPDATE agent_tasks SET status='queued',lease_until=NULL,updated_at=? WHERE agent_id=? AND status='running' AND lease_until<?",
            (_iso(now), agent_id, _iso(now)),
        )
        row = conn.execute(
            "SELECT id FROM agent_tasks WHERE agent_id=? AND status='queued' AND available_at<=? ORDER BY available_at,created_at LIMIT 1",
            (agent_id, _iso(now)),
        ).fetchone()
        if not row:
            return None
        updated = conn.execute(
            "UPDATE agent_tasks SET status='running',attempts=attempts+1,lease_until=?,updated_at=? WHERE id=? AND status='queued'",
            (_iso(lease_until), _iso(now), row["id"]),
        )
        if updated.rowcount != 1:
            return None
    return get_agent_task(row["id"])


def complete_agent_task(agent_id: str, task_id: str, status: str, result: dict[str, Any], error: str | None = None) -> dict[str, Any]:
    task = get_agent_task(task_id)
    if not task or task["agent_id"] != agent_id:
        raise ValueError("agent task not found")
    if task["status"] != "running":
        raise ValueError("agent task is not running")
    now = _now_dt()
    if status == "success":
        next_status = "success"
        available = task["available_at"]
    else:
        terminal = int(task["attempts"]) >= int(task["max_attempts"])
        next_status = "failed" if terminal else "queued"
        delay = min(300, 2 ** max(1, int(task["attempts"])))
        available = _iso(now + timedelta(seconds=delay))
    with connect() as conn:
        conn.execute(
            "UPDATE agent_tasks SET status=?,result_json=?,error=?,available_at=?,lease_until=NULL,updated_at=? WHERE id=?",
            (next_status, dumps(result), (error or "")[:4000] or None, available, _iso(now), task_id),
        )
    return get_agent_task(task_id) or task


def attach_artifact(task_id: str, artifact_id: str) -> dict[str, Any]:
    with connect() as conn:
        updated = conn.execute("UPDATE agent_tasks SET artifact_id=?,updated_at=? WHERE id=?", (artifact_id, _iso(), task_id))
    if updated.rowcount != 1:
        raise ValueError("agent task not found")
    return get_agent_task(task_id) or {"id": task_id}
