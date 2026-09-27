from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.db import connect, dumps, loads
from app.models import ActionRequest, DeploymentRequest, DeveloperSessionRequest, ServerSessionRequest, OperatorPlannedAction
from app.services.agents import enqueue_agent_task, get_agent, list_agents
from app.services.deployments import start_deployment
from app.services.executor import execute_action
from app.services.infrastructure import request_action as request_infrastructure_action
from app.services.projects import get_project, get_target, list_projects, list_targets
from app.services.registry import registry


def _now() -> str: return datetime.now(timezone.utc).isoformat()


def tool_catalog(organization_id: str | None) -> dict[str, Any]:
    projects=list_projects(organization_id)
    targets=list_targets(organization_id=organization_id)
    agents=list_agents(organization_id)
    apps=registry.list(organization_id)
    return {
        "projects":[{"id":p["id"],"name":p["name"],"source_agent_id":p["source_agent_id"],"runtime":p["runtime"]} for p in projects],
        "deployment_targets":[{"id":t["id"],"project_id":t["project_id"],"environment":t["environment"],"server_agent_id":t["server_agent_id"],"public_host":t["public_host"],"publish_mode":t["publish_mode"]} for t in targets],
        "agents":[{"id":a["id"],"name":a["name"],"kind":a["kind"],"status":a.get("effective_status",a["status"]),"capabilities":a["capabilities"]} for a in agents],
        "applications":[{"id":a.id,"name":a.name,"type":a.type,"capabilities":[c.name for c in a.capabilities]} for a in apps],
        "tools":{
            "project.deploy":{"required":["target_id"],"optional":["publish","allow_dirty","reason"]},
            "project.inspect":{"required":["project_id"]},
            "project.develop":{"required":["project_id","objective"],"optional":["auto_commit","auto_deploy_target_id"]},
            "server.diagnose":{"required":["agent_id","objective"],"optional":["max_iterations"]},
            "agent.task":{"required":["agent_id","capability"],"optional":["payload"]},
            "application.action":{"required":["app_id","capability"],"optional":["parameters","reason"]},
        },
    }


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+","",value.lower())


def _match_project(command: str, organization_id: str | None) -> dict[str,Any] | None:
    c=_norm(command); matches=[]
    for p in list_projects(organization_id):
        for candidate in (p["id"],p["name"]):
            n=_norm(candidate)
            if n and n in c: matches.append((len(n),p)); break
    return sorted(matches,key=lambda x:x[0],reverse=True)[0][1] if matches else None


def _match_agent(command: str, organization_id: str | None, kind: str | None=None) -> dict[str,Any] | None:
    c=_norm(command); matches=[]
    for a in list_agents(organization_id):
        if kind and a["kind"] not in {kind,"edge"}: continue
        for candidate in (a["id"],a["name"]):
            n=_norm(candidate)
            if n and n in c: matches.append((len(n),a)); break
    return sorted(matches,key=lambda x:x[0],reverse=True)[0][1] if matches else None


def _fallback_plan(command: str, organization_id: str | None) -> list[OperatorPlannedAction]:
    low=command.lower(); project=_match_project(command,organization_id)
    develop_words=("perbaiki","fix","benahi","kembangkan","develop","coding","audit kode","cari bug")
    if project and any(word in low for word in develop_words):
        auto_deploy=any(x in low for x in ("onlinekan","onlinkan","deploy","kirim ke server","produksi","production"))
        target_id=None
        if auto_deploy:
            targets=list_targets(project_id=project["id"],organization_id=organization_id)
            if not targets: raise ValueError("project has no deployment target for requested auto-deploy")
            target=next((t for t in targets if t["environment"]=="production"),targets[0]); target_id=target["id"]
        return [OperatorPlannedAction(tool="project.develop",parameters={"project_id":project["id"],"objective":command,"auto_commit":auto_deploy,"auto_deploy_target_id":target_id},rationale="matched autonomous developer intent")]
    deploy_words=("deploy","onlinekan","onlinkan","pasang","kirim ke server","sinkronkan","sync","publish")
    if project and any(word in low for word in deploy_words):
        targets=list_targets(project_id=project["id"],organization_id=organization_id)
        if not targets: raise ValueError("project has no deployment target")
        wanted="production" if any(x in low for x in ("produksi","production","onlinekan","onlinkan")) else None
        target=next((t for t in targets if wanted and t["environment"]==wanted),targets[0])
        publish=any(x in low for x in ("onlinekan","onlinkan","publish","internet","publik"))
        return [OperatorPlannedAction(tool="project.deploy",parameters={"target_id":target["id"],"publish":publish,"reason":command},rationale="matched project deployment intent")]
    if project and any(word in low for word in ("cek repo","periksa repo","status repo","inspect repo","cek source","periksa source")):
        return [OperatorPlannedAction(tool="project.inspect",parameters={"project_id":project["id"]},rationale="matched repository inspection intent")]
    if any(word in low for word in ("perbaiki server","benahi server","diagnosa server","diagnose server","cari masalah server","server lambat","server bermasalah","server error")):
        agent=_match_agent(command,organization_id,"server") or next((a for a in list_agents(organization_id) if a["kind"] in {"server","edge"}),None)
        if not agent: raise ValueError("no server agent is registered")
        return [OperatorPlannedAction(tool="server.diagnose",parameters={"agent_id":agent["id"],"objective":command},rationale="matched autonomous server troubleshooting intent")]
    if any(word in low for word in ("cek server","status server","kondisi server","server status")):
        agent=_match_agent(command,organization_id,"server") or next((a for a in list_agents(organization_id) if a["kind"] in {"server","edge"}),None)
        if not agent: raise ValueError("no server agent is registered")
        return [OperatorPlannedAction(tool="agent.task",parameters={"agent_id":agent["id"],"capability":"system.status","payload":{}},rationale="matched server status intent")]
    restart=re.search(r"(?:restart|mulai ulang|hidupkan ulang)\s+(?:service\s+)?([A-Za-z0-9_.@-]+)",command,re.I)
    if restart:
        agent=_match_agent(command,organization_id,"server") or next((a for a in list_agents(organization_id) if a["kind"] in {"server","edge"} and "service.restart" in a["capabilities"]),None)
        if not agent: raise ValueError("no server agent can restart services")
        return [OperatorPlannedAction(tool="agent.task",parameters={"agent_id":agent["id"],"capability":"service.restart","payload":{"service_name":restart.group(1)}},rationale="matched service restart intent")]
    # Preserve the deterministic application syntax as a last-resort power-user command.
    m=re.match(r"^\s*(?:run|jalankan)\s+([\w.-]+)\s+([\w.-]+)(?:\s+(\{.*\}))?\s*$",command,re.I|re.S)
    if m:
        app_id,capability,raw=m.groups(); params=json.loads(raw) if raw else {}
        if not registry.capability_exists(app_id,capability): raise ValueError("requested app/capability is not registered")
        return [OperatorPlannedAction(tool="application.action",parameters={"app_id":app_id,"capability":capability,"parameters":params,"reason":command},rationale="deterministic application action")]
    raise ValueError("command intent is not deterministic enough; configure an AI provider or name the project/server explicitly")


async def plan_operator(command: str, organization_id: str | None) -> list[OperatorPlannedAction]:
    if settings.ai_provider in {"router","openai-compatible"}:
        from app.ai.router import plan_operator_with_router
        actions=await plan_operator_with_router(command,tool_catalog(organization_id))
    else:
        actions=_fallback_plan(command,organization_id)
    if not actions: raise ValueError("operator plan is empty")
    # Deterministic post-AI validation: model output can never invent a node/project/capability.
    for action in actions:
        p=action.parameters
        if action.tool=="project.deploy":
            target=get_target(str(p.get("target_id","")))
            if not target or (organization_id is not None and target["organization_id"]!=organization_id): raise ValueError("operator planned an unavailable deployment target")
        elif action.tool in {"project.inspect","project.develop"}:
            project=get_project(str(p.get("project_id","")))
            if not project or (organization_id is not None and project["organization_id"]!=organization_id): raise ValueError("operator planned an unavailable project")
            if action.tool=="project.develop" and p.get("auto_deploy_target_id"):
                target=get_target(str(p["auto_deploy_target_id"]))
                if not target or target["project_id"]!=project["id"]: raise ValueError("operator planned an invalid developer auto-deploy target")
        elif action.tool=="server.diagnose":
            agent=get_agent(str(p.get("agent_id","")))
            if not agent or agent["kind"] not in {"server","edge"} or (organization_id is not None and agent["organization_id"]!=organization_id): raise ValueError("operator planned an unavailable server agent")
        elif action.tool=="agent.task":
            agent=get_agent(str(p.get("agent_id",""))); capability=str(p.get("capability",""))
            if not agent or (organization_id is not None and agent["organization_id"]!=organization_id) or capability not in agent["capabilities"]: raise ValueError("operator planned an unavailable agent capability")
        elif action.tool=="application.action":
            app=registry.get(str(p.get("app_id",""))); capability=str(p.get("capability",""))
            if not app or (organization_id is not None and app.organization_id!=organization_id) or not registry.capability_exists(app.id,capability): raise ValueError("operator planned an unavailable application capability")
    return actions


def _create_run(organization_id: str,actor: str,command: str,plan: list[OperatorPlannedAction],status: str="planned") -> str:
    rid=str(uuid4()); now=_now()
    with connect() as conn: conn.execute("INSERT INTO operator_runs(id,organization_id,actor,command,plan_json,status,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(rid,organization_id,actor,command,dumps([p.model_dump(mode="json") for p in plan]),status,dumps({}),now,now))
    return rid


def get_operator_run(run_id: str) -> dict[str,Any] | None:
    with connect() as conn: row=conn.execute("SELECT * FROM operator_runs WHERE id=?",(run_id,)).fetchone()
    if not row:return None
    item=dict(row);item["plan"]=loads(item.pop("plan_json"));item["result"]=loads(item.pop("result_json"));return item


def list_operator_runs(organization_id: str|None,limit:int=100) -> list[dict[str,Any]]:
    with connect() as conn:
        rows=conn.execute("SELECT * FROM operator_runs ORDER BY created_at DESC LIMIT ?",(max(1,min(limit,500)),)).fetchall() if organization_id is None else conn.execute("SELECT * FROM operator_runs WHERE organization_id=? ORDER BY created_at DESC LIMIT ?",(organization_id,max(1,min(limit,500)))).fetchall()
    out=[]
    for row in rows:
        item=dict(row);item["plan"]=loads(item.pop("plan_json"));item["result"]=loads(item.pop("result_json"));out.append(item)
    return out


async def execute_operator(command: str, organization_id: str, actor: str, dry_run: bool=False) -> dict[str,Any]:
    plan=await plan_operator(command,organization_id)
    run_id=_create_run(organization_id,actor,command,plan,"dry_run" if dry_run else "running")
    if dry_run:
        with connect() as conn: conn.execute("UPDATE operator_runs SET result_json=?,updated_at=? WHERE id=?",(dumps({"planned":len(plan)}),_now(),run_id))
        return get_operator_run(run_id) or {"id":run_id}
    results=[]; final="success"
    try:
        for action in plan:
            p=action.parameters
            if action.tool=="project.deploy":
                result=start_deployment(str(p["target_id"]),DeploymentRequest(reason=str(p.get("reason") or command),allow_dirty=bool(p.get("allow_dirty",False)),publish=bool(p.get("publish",False))),actor)
            elif action.tool=="project.inspect":
                project=get_project(str(p["project_id"])); assert project
                result=enqueue_agent_task(project["source_agent_id"],"repo.inspect",{"project_id":project["id"],"repo_path":project["repo_path"],"default_branch":project["default_branch"]},organization_id=organization_id,correlation_type="operator",correlation_id=run_id)
            elif action.tool=="project.develop":
                from app.services.developer import start_session
                result=start_session(DeveloperSessionRequest(project_id=str(p["project_id"]),objective=str(p.get("objective") or command),auto_commit=bool(p.get("auto_commit",False)),auto_deploy_target_id=p.get("auto_deploy_target_id")),actor,organization_id)
            elif action.tool=="server.diagnose":
                from app.services.server_ops import start_session as start_server_session
                result=start_server_session(ServerSessionRequest(agent_id=str(p["agent_id"]),objective=str(p.get("objective") or command),max_iterations=int(p.get("max_iterations",8))),actor,organization_id)
            elif action.tool=="agent.task":
                result=request_infrastructure_action(agent_id=str(p["agent_id"]),capability=str(p["capability"]),payload=dict(p.get("payload") or {}),organization_id=organization_id,actor=actor,reason=command,correlation_type="operator",correlation_id=run_id)
            elif action.tool=="application.action":
                ar=await execute_action(ActionRequest(app_id=str(p["app_id"]),capability=str(p["capability"]),parameters=dict(p.get("parameters") or {}),reason=str(p.get("reason") or command),actor=actor))
                result=ar.model_dump(mode="json")
            else: raise ValueError("unknown operator tool")
            results.append({"tool":action.tool,"result":result})
    except Exception as exc:
        final="failed";results.append({"error":str(exc)})
    with connect() as conn: conn.execute("UPDATE operator_runs SET status=?,result_json=?,updated_at=? WHERE id=?",(final,dumps({"actions":results}),_now(),run_id))
    return get_operator_run(run_id) or {"id":run_id,"status":final}
