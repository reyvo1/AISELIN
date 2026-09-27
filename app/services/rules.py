from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.db import connect, dumps, loads
from app.models import ActionRequest, EventIn, RuleCreate
from app.services.audit import audit
from app.services.executor import execute_action
from app.services.incidents import open_incident
from app.services.jobs import enqueue
from app.services.locks import acquire, release
from app.services.registry import registry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_rule(rule: RuleCreate) -> dict[str, Any]:
    rule_id = str(uuid4()); now = _now()
    app_id=rule.action.get("app_id")
    manifest=registry.get(app_id) if app_id else None
    organization_id=manifest.organization_id if manifest else "global"
    with connect() as conn:
        conn.execute("""INSERT INTO rules(id,name,enabled,trigger_type,trigger_json,condition_json,action_json,cooldown_seconds,last_fired_at,created_at,updated_at,organization_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rule_id, rule.name, int(rule.enabled), rule.trigger_type, dumps(rule.trigger), dumps(rule.condition), dumps(rule.action), rule.cooldown_seconds, None, now, now, organization_id))
    return {"id": rule_id, "organization_id": organization_id, **rule.model_dump(mode="json")}


def list_rules(organization_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM rules WHERE organization_id=? ORDER BY created_at DESC",(organization_id,)).fetchall() if organization_id is not None else conn.execute("SELECT * FROM rules ORDER BY created_at DESC").fetchall()
    out=[]
    for r in rows:
        item=dict(r); item["enabled"]=bool(item["enabled"]); item["trigger"]=loads(item.pop("trigger_json")); item["condition"]=loads(item.pop("condition_json")); item["action"]=loads(item.pop("action_json")); out.append(item)
    return out


def _get_path(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur: return None
        cur = cur[part]
    return cur


def _conditions_match(condition: dict[str, Any], payload: dict[str, Any]) -> bool:
    for path, spec in condition.items():
        actual = _get_path(payload, path)
        if not isinstance(spec, dict):
            if actual != spec: return False
            continue
        for op, expected in spec.items():
            if op == "eq" and actual != expected: return False
            if op == "ne" and actual == expected: return False
            if op == "gt" and not (actual is not None and actual > expected): return False
            if op == "gte" and not (actual is not None and actual >= expected): return False
            if op == "lt" and not (actual is not None and actual < expected): return False
            if op == "lte" and not (actual is not None and actual <= expected): return False
            if op == "contains" and not (actual is not None and expected in actual): return False
    return True


def _render(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict): return {k: _render(v, context) for k, v in value.items()}
    if isinstance(value, list): return [_render(v, context) for v in value]
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{event\.([\w.]+)\}", value)
        if match: return _get_path(context, match.group(1))
    return value


async def _run_rule_action(rule: dict[str, Any], action: dict[str, Any], reason: str) -> dict[str, Any]:
    delivery = action.pop("delivery", "sync")
    req=ActionRequest(app_id=action["app_id"], capability=action["capability"], parameters=action.get("parameters", {}), reason=reason, actor="autonomous-ai", idempotency_key=action.get("idempotency_key"))
    if delivery in {"queue", "durable"}:
        manifest=registry.get(req.app_id)
        try:
            job=enqueue("action", {"request": req.model_dump(mode="json")}, max_attempts=action.get("max_attempts"), organization_id=manifest.organization_id if manifest else "global")
            return {"status":"queued","job_id":job["id"],"app_id":req.app_id,"capability":req.capability}
        except RuntimeError as exc:
            return {"status":"blocked","message":str(exc),"app_id":req.app_id,"capability":req.capability}
    result=await execute_action(req)
    return result.model_dump(mode="json")


async def ingest_event(event: EventIn) -> dict[str, Any]:
    event_id=str(uuid4()); now=_now()
    manifest=registry.get(event.source_app_id) if event.source_app_id else None
    organization_id=manifest.organization_id if manifest else "global"
    with connect() as conn: conn.execute("INSERT INTO events(id,ts,source_app_id,event_type,payload_json,organization_id) VALUES(?,?,?,?,?,?)",(event_id,now,event.source_app_id,event.event_type,dumps(event.payload),organization_id))
    fired=[]
    for rule in list_rules(organization_id):
        if not rule["enabled"] or rule["trigger_type"] != "event": continue
        trig=rule["trigger"]
        if trig.get("event_type") != event.event_type: continue
        source=trig.get("source_app_id")
        if source and source != event.source_app_id: continue
        if not _conditions_match(rule["condition"], event.payload): continue
        if rule["last_fired_at"] and rule["cooldown_seconds"]:
            last_dt=datetime.fromisoformat(rule["last_fired_at"])
            if (datetime.now(timezone.utc)-last_dt).total_seconds() < rule["cooldown_seconds"]: continue
        action=_render(dict(rule["action"]), event.payload)
        result=await _run_rule_action(rule, action, f"autonomous rule: {rule['name']}")
        fired.append({"rule_id":rule["id"],"result":result})
        with connect() as conn: conn.execute("UPDATE rules SET last_fired_at=?,updated_at=? WHERE id=?",(now,now,rule["id"]))
    if event.payload.get("severity") in {"critical","high"}:
        manifest=registry.get(event.source_app_id) if event.source_app_id else None
        open_incident(organization_id=organization_id,app_id=event.source_app_id,severity=event.payload.get("severity","high"),title=event.payload.get("title",event.event_type),description=event.payload.get("message",f"Event {event.event_type}"),fingerprint=event.payload.get("fingerprint",f"event:{event.source_app_id}:{event.event_type}"),source_event_id=event_id,metadata=event.payload)
    audit(actor="event-bus", app_id=event.source_app_id, capability=None, action="event.ingest", decision="accepted", reason=event.event_type, request=event.model_dump(mode="json"), result={"fired":fired})
    return {"event_id":event_id,"fired":fired}


async def run_due_schedules(worker_id: str="scheduler") -> list[dict[str, Any]]:
    if not acquire("scheduler",worker_id,lease_seconds=55): return []
    try:
        now_dt=datetime.now(timezone.utc); fired=[]
        for rule in list_rules():
            if not rule["enabled"] or rule["trigger_type"] != "schedule": continue
            interval=int(rule["trigger"].get("interval_seconds",0))
            if interval < 60: continue
            last=rule["last_fired_at"]
            if last and (now_dt-datetime.fromisoformat(last)).total_seconds() < max(interval,rule["cooldown_seconds"]): continue
            action=dict(rule["action"])
            result=await _run_rule_action(rule,action,f"scheduled rule: {rule['name']}")
            fired.append({"rule_id":rule["id"],"result":result}); now=_now()
            with connect() as conn: conn.execute("UPDATE rules SET last_fired_at=?,updated_at=? WHERE id=?",(now,now,rule["id"]))
        return fired
    finally:
        release("scheduler",worker_id)
