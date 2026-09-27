from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.db import connect, dumps, loads
from app.models import ServerPlannedAction, ServerSessionRequest
from app.services.agents import get_agent, get_agent_task
from app.services.infrastructure import request_action as request_infrastructure_action
from app.services.audit import audit
from app.services.incidents import open_incident


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _incident_if_terminal(session: dict[str, Any]) -> None:
    if session.get("status") not in {"failed", "blocked_policy", "blocked_capability", "blocked_ai"}:
        return
    open_incident(
        organization_id=session["organization_id"], severity="high",
        title=f"Server automation stopped: {session['agent_id']}",
        description=str((session.get("result") or {}).get("error") or session.get("objective") or "server automation stopped")[:8000],
        fingerprint=f"server-session:{session['agent_id']}:{session.get('phase')}",
        metadata={"session_id":session["id"],"agent_id":session["agent_id"],"phase":session.get("phase"),"objective":session.get("objective")},
    )


def _decode(row) -> dict[str, Any]:
    item = dict(row)
    item["context"] = loads(item.pop("context_json"))
    item["result"] = loads(item.pop("result_json"))
    return item


def get_session(session_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM server_sessions WHERE id=?", (session_id,)).fetchone()
    return _decode(row) if row else None


def list_sessions(organization_id: str | None = None, agent_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if organization_id is not None:
        where.append("organization_id=?"); params.append(organization_id)
    if agent_id is not None:
        where.append("agent_id=?"); params.append(agent_id)
    sql = "SELECT * FROM server_sessions" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_decode(row) for row in rows]


def _update(session_id: str, **changes) -> dict[str, Any]:
    session = get_session(session_id)
    if not session:
        raise ValueError("server session not found")
    fields = {
        "status": session["status"], "phase": session["phase"], "iteration": session["iteration"],
        "current_task_id": session["current_task_id"], "context": session["context"], "result": session["result"],
        "finished_at": session["finished_at"],
    }
    fields.update(changes)
    with connect() as conn:
        conn.execute(
            "UPDATE server_sessions SET status=?,phase=?,iteration=?,current_task_id=?,context_json=?,result_json=?,updated_at=?,finished_at=? WHERE id=?",
            (fields["status"], fields["phase"], fields["iteration"], fields["current_task_id"], dumps(fields["context"]), dumps(fields["result"]), _now(), fields["finished_at"], session_id),
        )
    updated = get_session(session_id) or session
    if updated.get("finished_at"):
        _incident_if_terminal(updated)
    return updated


def _queue(session: dict[str, Any], capability: str, payload: dict[str, Any], phase: str) -> dict[str, Any]:
    decision = request_infrastructure_action(
        agent_id=session["agent_id"],
        capability=capability,
        payload=payload,
        organization_id=session["organization_id"],
        actor=session["actor"],
        reason=session["objective"],
        correlation_type="server_session",
        correlation_id=session["id"],
        correlation_phase=phase,
    )
    if decision["status"] == "queued":
        task = decision["task"]
        return _update(
            session["id"], status="running", phase=phase, current_task_id=task["id"],
            result={**session["result"], "infrastructure_policy": decision.get("policy")},
        )
    if decision["status"] == "approval_required":
        return _update(
            session["id"], status="waiting_approval", phase=phase, current_task_id=None,
            result={**session["result"], "infrastructure_approval_id": decision["approval_id"], "pending_capability": capability},
        )
    return _update(
        session["id"], status="blocked_policy", phase="blocked_policy", current_task_id=None,
        result={**session["result"], "error": decision.get("message") or "infrastructure action blocked by policy", "pending_capability": capability},
        finished_at=_now(),
    )


def attach_approved_task(session_id: str, task_id: str, phase: str) -> dict[str, Any]:
    session = get_session(session_id)
    if not session:
        raise ValueError("server session not found")
    if session["status"] != "waiting_approval":
        raise ValueError("server session is not waiting for approval")
    task = get_agent_task(task_id)
    if not task or task.get("correlation_type") != "server_session" or task.get("correlation_id") != session_id:
        raise ValueError("approved task does not belong to server session")
    result = dict(session["result"] or {})
    result.pop("infrastructure_approval_id", None)
    result.pop("pending_capability", None)
    result["approved_task_id"] = task_id
    return _update(session_id, status="running", phase=phase, current_task_id=task_id, result=result)


def reject_waiting_approval(session_id: str, note: str = "") -> dict[str, Any]:
    session = get_session(session_id)
    if not session:
        raise ValueError("server session not found")
    if session["status"] != "waiting_approval":
        raise ValueError("server session is not waiting for approval")
    return _update(
        session_id, status="rejected", phase="approval_rejected", current_task_id=None,
        result={**session["result"], "error": note or "infrastructure approval rejected"}, finished_at=_now(),
    )


def start_session(request: ServerSessionRequest, actor: str, organization_id: str) -> dict[str, Any]:
    agent = get_agent(request.agent_id)
    if not agent or agent["organization_id"] != organization_id or agent["kind"] not in {"server", "edge"}:
        raise ValueError("server agent not found in organization")
    if "system.status" not in agent["capabilities"]:
        raise ValueError("server agent must declare system.status")
    session_id = str(uuid4()); now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO server_sessions(id,organization_id,agent_id,actor,objective,status,phase,iteration,max_iterations,current_task_id,context_json,result_json,created_at,updated_at,finished_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, organization_id, agent["id"], actor, request.objective, "running", "system_status", 0, request.max_iterations, None, dumps({"history": []}), dumps({}), now, now, None),
        )
    session = get_session(session_id)
    if not session:
        raise RuntimeError("server session creation failed")
    session = _queue(session, "system.status", {}, "system_status")
    audit(actor=actor, app_id=None, capability=None, action="server_session.start", decision="queued", reason=request.objective, request={"agent_id":agent["id"],"session_id":session_id}, result={}, organization_id=organization_id)
    return session


def _bounded_context(context: dict[str, Any]) -> dict[str, Any]:
    history = list(context.get("history", []))[-24:]
    trimmed = []
    for item in history:
        copy = dict(item); result = copy.get("result")
        if isinstance(result, dict):
            for key in ("stdout", "stderr", "logs", "containers", "processes"):
                value = result.get(key)
                if isinstance(value, str) and len(value) > 30000:
                    result[key] = value[:30000] + "...[truncated]"
                elif isinstance(value, list) and len(value) > 500:
                    result[key] = value[:500]
        trimmed.append(copy)
    return {**context, "history": trimmed}


def _capability_for(action: ServerPlannedAction) -> tuple[str | None, dict[str, Any]]:
    p = dict(action.parameters or {})
    mapping = {
        "system_status": "system.status",
        "process_list": "system.processes",
        "network_sockets": "network.sockets",
        "network_routes": "network.routes",
        "firewall_status": "firewall.status",
        "package_updates": "package.updates",
        "package_security_updates": "package.security_updates",
        "package_apply_updates": "package.apply_updates",
        "service_status": "service.status",
        "service_logs": "service.logs",
        "service_restart": "service.restart",
        "docker_list": "docker.list",
        "docker_logs": "docker.logs",
        "docker_restart": "docker.restart",
    }
    capability = mapping.get(action.action)
    if capability in {"service.status", "service.logs", "service.restart"}:
        name = str(p.get("service_name") or "")
        if not name:
            raise ValueError(f"{action.action} requires service_name")
        p = {"service_name": name}
        if capability == "service.logs":
            p["lines"] = max(1, min(int(action.parameters.get("lines", 200)), 2000))
    if capability == "system.processes":
        p = {"limit": max(1, min(int(p.get("limit", 30)), 200)), "sort": str(p.get("sort") or "cpu")}
    if capability == "network.sockets":
        p = {"listening_only": bool(p.get("listening_only", True))}
    if capability == "package.apply_updates":
        packages = p.get("packages") or []
        if not isinstance(packages, list) or not packages:
            raise ValueError("package_apply_updates requires packages")
        p = {"packages": [str(x) for x in packages[:50]]}
    if capability in {"docker.logs", "docker.restart"}:
        name = str(p.get("container_name") or "")
        if not name:
            raise ValueError(f"{action.action} requires container_name")
        p = {"container_name": name}
        if capability == "docker.logs":
            p["lines"] = max(1, min(int(action.parameters.get("lines", 200)), 2000))
    return capability, p


async def _plan_next(session: dict[str, Any]) -> dict[str, Any]:
    if int(session["iteration"]) >= int(session["max_iterations"]):
        return _update(session["id"], status="failed", phase="iteration_limit", current_task_id=None, result={**session["result"], "error":"server troubleshooting iteration limit reached"}, finished_at=_now())
    if settings.ai_provider not in {"router", "openai-compatible"}:
        return _update(session["id"], status="blocked_ai", phase="blocked_ai", current_task_id=None, result={**session["result"], "error":"autonomous server troubleshooting requires AIOC_AI_PROVIDER=router/openai-compatible"}, finished_at=_now())
    agent = get_agent(session["agent_id"])
    if not agent:
        return _update(session["id"], status="failed", phase="failed", result={"error":"server agent disappeared"}, finished_at=_now())
    from app.ai.router import plan_server_with_router
    action = await plan_server_with_router(session["objective"], {"id":agent["id"],"name":agent["name"],"capabilities":agent["capabilities"],"metadata":agent["metadata"]}, _bounded_context(session["context"]))
    session = _update(session["id"], iteration=int(session["iteration"])+1, result={**session["result"], "last_plan": action.model_dump(mode="json")})
    if action.action == "fail":
        return _update(session["id"], status="failed", phase="failed", current_task_id=None, result={**session["result"], "reason":action.rationale}, finished_at=_now())
    if action.action == "finish":
        return _update(session["id"], status="success", phase="complete", current_task_id=None, result={**session["result"], "summary":action.rationale}, finished_at=_now())
    capability, payload = _capability_for(action)
    if not capability or capability not in agent["capabilities"]:
        return _update(session["id"], status="blocked_capability", phase="blocked_capability", current_task_id=None, result={**session["result"], "error":f"planner requested unavailable server capability: {capability}"}, finished_at=_now())
    return _queue(session, capability, payload, action.action)


async def advance_from_agent_task(task_id: str) -> dict[str, Any] | None:
    task = get_agent_task(task_id)
    if not task or task.get("correlation_type") != "server_session" or not task.get("correlation_id"):
        return None
    session = get_session(task["correlation_id"])
    if not session or session["current_task_id"] != task_id:
        return session
    if task["status"] == "queued":
        return session
    context = dict(session["context"]); history = list(context.get("history", []))
    if task["status"] == "failed":
        history.append({"phase":session["phase"],"status":"failed","error":task.get("error"),"result":task.get("result")}); context["history"] = history
        session = _update(session["id"], current_task_id=None, context=context)
        # Inspection/action failure is evidence. Let the AI choose another safe step until bounded limit.
        return await _plan_next(session)
    if task["status"] != "success":
        return session
    result = task.get("result") or {}
    history.append({"phase":session["phase"],"status":"success","result":result}); context["history"] = history
    session = _update(session["id"], current_task_id=None, context=context)
    return await _plan_next(session)
