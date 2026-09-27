from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.db import connect, dumps, loads
from app.models import DeploymentRequest, DeveloperPlannedAction, DeveloperSessionRequest
from app.services.agents import enqueue_agent_task, get_agent_task, get_agent
from app.services.audit import audit
from app.services.deployments import start_deployment
from app.services.projects import get_project, get_target
from app.services.locks import acquire as acquire_lock, release as release_lock


def _now() -> str: return datetime.now(timezone.utc).isoformat()

def _lock_name(project_id: str) -> str: return f"engineering:project:{project_id}"
def _lock_owner(session_id: str) -> str: return f"developer:{session_id}"
def _renew_lock(session: dict[str,Any]) -> bool: return acquire_lock(_lock_name(session["project_id"]),_lock_owner(session["id"]),max(60,settings.deployment_timeout_seconds))
def _release_lock(session: dict[str,Any]) -> None: release_lock(_lock_name(session["project_id"]),_lock_owner(session["id"]))
def _terminal(session: dict[str,Any], **changes) -> dict[str,Any]:
    updated=_update(session["id"],**changes); _release_lock(updated); return updated


def _decode(row) -> dict[str,Any]:
    item=dict(row); item["context"]=loads(item.pop("context_json")); item["result"]=loads(item.pop("result_json")); item["auto_commit"]=bool(item["auto_commit"]); return item


def get_session(session_id: str) -> dict[str,Any] | None:
    with connect() as conn: row=conn.execute("SELECT * FROM developer_sessions WHERE id=?",(session_id,)).fetchone()
    return _decode(row) if row else None


def list_sessions(organization_id: str|None=None,project_id: str|None=None,limit:int=100) -> list[dict[str,Any]]:
    where=[];params=[]
    if organization_id is not None: where.append("organization_id=?");params.append(organization_id)
    if project_id is not None: where.append("project_id=?");params.append(project_id)
    sql="SELECT * FROM developer_sessions"+(" WHERE "+" AND ".join(where) if where else "")+" ORDER BY created_at DESC LIMIT ?";params.append(max(1,min(limit,500)))
    with connect() as conn: rows=conn.execute(sql,tuple(params)).fetchall()
    return [_decode(r) for r in rows]


def _update(session_id: str, **changes) -> dict[str,Any]:
    session=get_session(session_id)
    if not session: raise ValueError("developer session not found")
    values={
        "status":changes.get("status",session["status"]),"phase":changes.get("phase",session["phase"]),"iteration":changes.get("iteration",session["iteration"]),
        "baseline_revision":changes.get("baseline_revision",session["baseline_revision"]),"current_task_id":changes.get("current_task_id",session["current_task_id"]),
        "context":changes.get("context",session["context"]),"result":changes.get("result",session["result"]),"finished_at":changes.get("finished_at",session["finished_at"]),
    }
    with connect() as conn:
        conn.execute("UPDATE developer_sessions SET status=?,phase=?,iteration=?,baseline_revision=?,current_task_id=?,context_json=?,result_json=?,updated_at=?,finished_at=? WHERE id=?",
                     (values["status"],values["phase"],values["iteration"],values["baseline_revision"],values["current_task_id"],dumps(values["context"]),dumps(values["result"]),_now(),values["finished_at"],session_id))
    return get_session(session_id) or session


def _queue(session: dict[str,Any],capability: str,payload: dict[str,Any],phase: str) -> dict[str,Any]:
    project=get_project(session["project_id"])
    if not project: raise ValueError("project not found")
    task=enqueue_agent_task(project["source_agent_id"],capability,payload,organization_id=session["organization_id"],correlation_type="developer",correlation_id=session["id"])
    return _update(session["id"],phase=phase,current_task_id=task["id"])


def start_session(request: DeveloperSessionRequest, actor: str, organization_id: str) -> dict[str,Any]:
    project=get_project(request.project_id)
    if not project or project["organization_id"]!=organization_id: raise ValueError("project not found in organization")
    source=get_agent(project["source_agent_id"])
    required={"repo.context","repo.read","repo.search","repo.apply_patch","repo.run_steps"}
    if request.auto_commit: required.add("repo.commit")
    if not source or not required.issubset(set(source["capabilities"])):
        missing=required-set(source["capabilities"] if source else [])
        raise ValueError(f"source agent missing developer capabilities: {sorted(missing)}")
    if request.auto_deploy_target_id:
        target=get_target(request.auto_deploy_target_id)
        if not target or target["project_id"]!=project["id"] or target["organization_id"]!=organization_id: raise ValueError("auto deploy target does not belong to project")
        if not request.auto_commit: raise ValueError("auto deploy requires auto_commit so deployment has a clean immutable revision")
    session_id=str(uuid4()); now=_now()
    if not acquire_lock(_lock_name(project["id"]),_lock_owner(session_id),max(60,settings.deployment_timeout_seconds)):
        raise ValueError("project is busy with another engineering/deployment operation")
    with connect() as conn:
        conn.execute("""INSERT INTO developer_sessions(id,organization_id,project_id,actor,objective,status,phase,iteration,max_iterations,auto_commit,auto_deploy_target_id,baseline_revision,current_task_id,context_json,result_json,created_at,updated_at,finished_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (session_id,organization_id,project["id"],actor,request.objective,"running","context",0,request.max_iterations,1 if request.auto_commit else 0,request.auto_deploy_target_id,None,None,dumps({"history":[],"patch_count":0,"verified_after_patch":False}),dumps({}),now,now,None))
    session=get_session(session_id)
    if not session:
        release_lock(_lock_name(project["id"]),_lock_owner(session_id)); raise RuntimeError("developer session creation failed")
    session=_queue(session,"repo.context",{"project_id":project["id"],"repo_path":project["repo_path"]},"context")
    audit(actor=actor,app_id=None,capability=None,action="developer.start",decision="queued",reason=request.objective,request={"project_id":project["id"],"session_id":session_id},result={},organization_id=organization_id)
    return session


def _bounded_context(context: dict[str,Any]) -> dict[str,Any]:
    # Keep AI context bounded even after many iterations.
    history=list(context.get("history",[]))[-20:]
    trimmed=[]
    for item in history:
        copy=dict(item)
        result=copy.get("result")
        if isinstance(result,dict):
            for key in ("content","stdout","stderr","matches","tree"):
                if key in result and isinstance(result[key],str) and len(result[key])>30000: result[key]=result[key][:30000]+"...[truncated]"
                if key in result and isinstance(result[key],list) and len(result[key])>500: result[key]=result[key][:500]
        trimmed.append(copy)
    return {**context,"history":trimmed}


async def _plan_next(session: dict[str,Any]) -> dict[str,Any]:
    if settings.ai_provider not in {"router","openai-compatible"}:
        result={"error":"autonomous code repair requires AIOC_AI_PROVIDER=router/openai-compatible"}
        return _terminal(session,status="blocked_ai",phase="blocked_ai",current_task_id=None,result=result,finished_at=_now())
    project=get_project(session["project_id"])
    if not project: return _terminal(session,status="failed",phase="failed",result={"error":"project disappeared"},finished_at=_now())
    if int(session["iteration"])>=int(session["max_iterations"]):
        return _terminal(session,status="failed",phase="iteration_limit",current_task_id=None,result={"error":"developer iteration limit reached"},finished_at=_now())
    from app.ai.router import plan_developer_with_router
    context=_bounded_context(session["context"])
    action:DeveloperPlannedAction=await plan_developer_with_router(session["objective"],{
        "id":project["id"],"name":project["name"],"runtime":project["runtime"],"default_branch":project["default_branch"],"test_steps":project["test_steps"]
    },context)
    iteration=int(session["iteration"])+1
    session=_update(session["id"],iteration=iteration,result={**session["result"],"last_plan":action.model_dump(mode="json")})
    p=action.parameters
    if action.action=="read":
        path=str(p.get("path") or "")
        if not path: raise ValueError("developer read action requires path")
        return _queue(session,"repo.read",{"project_id":project["id"],"repo_path":project["repo_path"],"path":path},"read")
    if action.action=="search":
        query=str(p.get("query") or "")
        if not query: raise ValueError("developer search action requires query")
        return _queue(session,"repo.search",{"project_id":project["id"],"repo_path":project["repo_path"],"query":query},"search")
    if action.action=="patch":
        patch=str(p.get("patch") or "")
        if not patch or len(patch)>1_000_000: raise ValueError("developer patch is empty or too large")
        return _queue(session,"repo.apply_patch",{"project_id":project["id"],"repo_path":project["repo_path"],"patch":patch},"patch")
    if action.action=="test":
        return _queue(session,"repo.run_steps",{"project_id":project["id"],"repo_path":project["repo_path"],"steps":project["test_steps"]},"test")
    if action.action=="fail":
        return _terminal(session,status="failed",phase="failed",current_task_id=None,result={**session["result"],"reason":action.rationale},finished_at=_now())
    if action.action=="finish":
        context=session["context"]
        if int(context.get("patch_count",0))>0 and not bool(context.get("verified_after_patch")):
            return _queue(session,"repo.run_steps",{"project_id":project["id"],"repo_path":project["repo_path"],"steps":project["test_steps"]},"final_test")
        return await _finish(session,action.rationale)
    raise ValueError("unsupported developer planner action")


async def _finish(session: dict[str,Any], rationale: str) -> dict[str,Any]:
    project=get_project(session["project_id"])
    if not project: raise ValueError("project not found")
    if session["auto_commit"]:
        paths=list(session["context"].get("modified_paths",[]))
        return _queue(session,"repo.commit",{"project_id":project["id"],"repo_path":project["repo_path"],"message":f"AIOC: {session['objective'][:120]}","paths":paths},"commit")
    result={**session["result"],"summary":rationale,"verified":bool(session["context"].get("verified_after_patch")) or int(session["context"].get("patch_count",0))==0}
    return _terminal(session,status="success",phase="complete",current_task_id=None,result=result,finished_at=_now())


async def advance_from_agent_task(task_id: str) -> dict[str,Any] | None:
    task=get_agent_task(task_id)
    if not task or task.get("correlation_type")!="developer" or not task.get("correlation_id"): return None
    session=get_session(task["correlation_id"])
    if not session or session["current_task_id"]!=task_id:return session
    if task["status"]=="queued":return session
    if task["status"]=="failed":
        context=dict(session["context"]); history=list(context.get("history",[])); history.append({"phase":session["phase"],"status":"failed","error":task.get("error"),"result":task.get("result")}); context["history"]=history
        session=_update(session["id"],context=context,current_task_id=None)
        # Test/patch failures are useful evidence; let the AI reason again while budget remains.
        if session["phase"] in {"patch","test","final_test","read","search"}: return await _plan_next(session)
        return _terminal(session,status="failed",phase="failed",result={"error":task.get("error")},finished_at=_now())
    if task["status"]!="success":return session
    result=task.get("result") or {}; context=dict(session["context"]); history=list(context.get("history",[])); history.append({"phase":session["phase"],"status":"success","result":result}); context["history"]=history
    phase=session["phase"]
    if phase=="context":
        if bool(result.get("dirty")):
            return _terminal(session,status="blocked_dirty",phase="blocked_dirty",current_task_id=None,baseline_revision=str(result.get("revision") or ""),context={**context,"initial":result},result={"error":"autonomous repair requires a clean repository baseline"},finished_at=_now())
        session=_update(session["id"],baseline_revision=str(result.get("revision") or ""),context={**context,"initial":result},current_task_id=None)
        return await _plan_next(session)
    if phase=="patch":
        context["patch_count"]=int(context.get("patch_count",0))+1; context["verified_after_patch"]=False
        modified=set(context.get("modified_paths",[])); modified.update(result.get("touched_paths") or []); context["modified_paths"]=sorted(modified)
    elif phase in {"test","final_test"}:
        context["verified_after_patch"]=True
    session=_update(session["id"],context=context,current_task_id=None)
    if phase=="final_test": return await _finish(session,"objective completed; mandatory post-patch verification passed")
    if phase=="commit":
        revision=str(result.get("revision") or "")
        remaining=list(result.get("remaining_changes") or [])
        session=_update(session["id"],result={**session["result"],"committed_revision":revision,"remaining_changes":remaining},current_task_id=None)
        if session.get("auto_deploy_target_id") and bool(result.get("dirty")):
            return _terminal(
                session,status="blocked_dirty_after_commit",phase="blocked_dirty_after_commit",
                result={**session["result"],"error":"repository contains uncommitted/generated files after autonomous commit; auto-deploy blocked","remaining_changes":remaining},
                finished_at=_now(),
            )
        if session.get("auto_deploy_target_id"):
            _release_lock(session)
            deployment=start_deployment(session["auto_deploy_target_id"],DeploymentRequest(reason=f"developer session {session['id']}",allow_dirty=False,publish=True),session["actor"])
            return _update(session["id"],status="success",phase="complete",result={**session["result"],"deployment_run_id":deployment["id"]},finished_at=_now())
        return _terminal(session,status="success",phase="complete",finished_at=_now())
    return await _plan_next(session)
