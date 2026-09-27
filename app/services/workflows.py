from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.db import connect, dumps, loads
from app.models import ActionRequest, WorkflowCreate
from app.services.audit import audit
from app.services.executor import execute_action
from app.services.registry import registry


def _now() -> str: return datetime.now(timezone.utc).isoformat()

def _decode_workflow(row) -> dict:
    x=dict(row); x["definition"]=loads(x.pop("definition_json")); x["enabled"]=bool(x["enabled"]); return x


def _validate_definition(item: WorkflowCreate) -> None:
    step_ids={s.id for s in item.steps}; graph={s.id:set(s.depends_on) for s in item.steps}; visiting=set(); visited=set()
    def visit(node: str):
        if node in visited:return
        if node in visiting:raise ValueError("workflow dependency cycle detected")
        visiting.add(node)
        for dep in graph[node]:
            if dep not in step_ids:raise ValueError(f"unknown workflow dependency: {dep}")
            visit(dep)
        visiting.remove(node);visited.add(node)
    for sid in step_ids:visit(sid)
    for step in item.steps:
        manifest=registry.get(step.app_id)
        if not manifest:raise ValueError(f"application not found: {step.app_id}")
        if manifest.organization_id!=item.organization_id:raise ValueError("workflow steps must stay inside one organization")
        if not registry.capability_exists(step.app_id,step.capability):raise ValueError(f"capability not found: {step.app_id}/{step.capability}")


def create_workflow(item: WorkflowCreate) -> dict:
    _validate_definition(item); workflow_id=str(uuid4()); now=_now(); definition=item.model_dump(mode="json")
    with connect() as conn:
        conn.execute("""INSERT INTO workflows(id,organization_id,name,description,definition_json,enabled,version,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,1,?,?)""",(workflow_id,item.organization_id,item.name,item.description,dumps(definition),int(item.enabled),now,now))
    return get_workflow(workflow_id) or {"id":workflow_id}


def update_workflow(workflow_id: str,item: WorkflowCreate) -> dict:
    _validate_definition(item); current=get_workflow(workflow_id)
    if not current:raise ValueError("workflow not found")
    if current["organization_id"]!=item.organization_id:raise ValueError("workflow organization cannot be changed")
    version=int(current["version"])+1
    with connect() as conn:
        conn.execute("UPDATE workflows SET name=?,description=?,definition_json=?,enabled=?,version=?,updated_at=? WHERE id=?",(item.name,item.description,dumps(item.model_dump(mode="json")),int(item.enabled),version,_now(),workflow_id))
    return get_workflow(workflow_id) or {"id":workflow_id}


def set_workflow_enabled(workflow_id: str,enabled: bool) -> dict|None:
    with connect() as conn: result=conn.execute("UPDATE workflows SET enabled=?,updated_at=? WHERE id=?",(int(enabled),_now(),workflow_id))
    return get_workflow(workflow_id) if result.rowcount else None


def get_workflow(workflow_id: str) -> dict | None:
    with connect() as conn:row=conn.execute("SELECT * FROM workflows WHERE id=?",(workflow_id,)).fetchone()
    return _decode_workflow(row) if row else None


def list_workflows(organization_id: str | None=None) -> list[dict]:
    with connect() as conn:
        rows=conn.execute("SELECT * FROM workflows WHERE organization_id=? ORDER BY name",(organization_id,)).fetchall() if organization_id is not None else conn.execute("SELECT * FROM workflows ORDER BY organization_id,name").fetchall()
    return [_decode_workflow(r) for r in rows]


def create_run(workflow_id: str,actor: str,reason: str,inputs: dict[str,Any],dry_run: bool=False,timeout_seconds:int|None=None) -> dict:
    wf=get_workflow(workflow_id)
    if not wf or not wf["enabled"]:raise ValueError("workflow not found or disabled")
    run_id=str(uuid4());now=_now();run_inputs={**inputs,"_dry_run":bool(dry_run),"_timeout_seconds":timeout_seconds}
    with connect() as conn:
        conn.execute("""INSERT INTO workflow_runs(id,workflow_id,organization_id,status,actor,reason,inputs_json,result_json,started_at,finished_at,cancel_requested,workflow_version,workflow_definition_json)
                     VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)""",(run_id,workflow_id,wf["organization_id"],"queued",actor,reason,dumps(run_inputs),dumps({}),now,None,int(wf["version"]),dumps(wf["definition"])))
    return get_run(run_id) or {"id":run_id}


def get_run(run_id: str) -> dict | None:
    with connect() as conn:
        row=conn.execute("SELECT * FROM workflow_runs WHERE id=?",(run_id,)).fetchone();steps=conn.execute("SELECT * FROM workflow_step_runs WHERE workflow_run_id=? ORDER BY started_at",(run_id,)).fetchall()
    if not row:return None
    x=dict(row);x["inputs"]=loads(x.pop("inputs_json"));x["result"]=loads(x.pop("result_json"));x["workflow_definition"]=loads(x.pop("workflow_definition_json"));x["cancel_requested"]=bool(x["cancel_requested"]);x["steps"]=[]
    for sr in steps:
        y=dict(sr);y["result"]=loads(y.pop("result_json"));x["steps"].append(y)
    return x


def list_runs(organization_id: str | None=None,limit: int=100) -> list[dict]:
    limit=max(1,min(limit,500))
    with connect() as conn:
        rows=conn.execute("SELECT id FROM workflow_runs WHERE organization_id=? ORDER BY started_at DESC LIMIT ?",(organization_id,limit)).fetchall() if organization_id is not None else conn.execute("SELECT id FROM workflow_runs ORDER BY started_at DESC LIMIT ?",(limit,)).fetchall()
    return [r for rid in rows if (r:=get_run(rid["id"]))]


def cancel_run(run_id: str) -> dict|None:
    run=get_run(run_id)
    if not run:return None
    if run["status"] in {"success","cancelled"}:return run
    now=_now(); terminal=run["status"] in {"queued","waiting_approval","failed"}
    with connect() as conn:
        conn.execute("UPDATE workflow_runs SET cancel_requested=1,status=?,finished_at=COALESCE(finished_at,?) WHERE id=?",("cancelled" if terminal else run["status"],now,run_id))
    return get_run(run_id)


def prepare_resume(run_id: str) -> dict:
    run=get_run(run_id)
    if not run:raise ValueError("workflow run not found")
    if run["status"] not in {"waiting_approval","failed","cancelled"}:raise ValueError("workflow run is not resumable")
    with connect() as conn:conn.execute("UPDATE workflow_runs SET status='queued',cancel_requested=0,finished_at=NULL WHERE id=?",(run_id,))
    return get_run(run_id) or run


def _render(value: Any,inputs: dict[str,Any],step_results: dict[str,Any]) -> Any:
    if isinstance(value,dict):return {k:_render(v,inputs,step_results) for k,v in value.items()}
    if isinstance(value,list):return [_render(v,inputs,step_results) for v in value]
    if isinstance(value,str):
        if value.startswith("${input.") and value.endswith("}"):return inputs.get(value[8:-1])
        if value.startswith("${step.") and value.endswith("}"):
            parts=value[7:-1].split(".",1);cur:Any=step_results.get(parts[0],{})
            if len(parts)==1:return cur
            for key in parts[1].split("."):
                if not isinstance(cur,dict):return None
                cur=cur.get(key)
            return cur
    return value


async def execute_run(run_id: str) -> dict:
    run=get_run(run_id)
    if not run:raise ValueError("workflow run not found")
    if run["cancel_requested"] or run["status"]=="cancelled":return run
    definition=run["workflow_definition"] or (get_workflow(run["workflow_id"]) or {}).get("definition")
    if not definition:raise ValueError("workflow definition unavailable")
    steps={s["id"]:s for s in definition["steps"]};pending=set(steps);statuses={};results={};completed=[]
    for sr in run["steps"]:
        sid=sr["step_id"]
        if sr["status"]=="success":statuses[sid]="success";results[sid]=sr["result"]
    pending-=set(statuses)
    dry_run=bool(run["inputs"].get("_dry_run",False));timeout=run["inputs"].get("_timeout_seconds");inputs={k:v for k,v in run["inputs"].items() if not k.startswith("_")}
    max_parallel=max(1,min(int(definition.get("max_parallel_steps",1)),16)); started=datetime.fromisoformat(run["started_at"])
    with connect() as conn:conn.execute("UPDATE workflow_runs SET status='running' WHERE id=?",(run_id,))
    final_status="success";failure_message=""

    async def execute_step(sid: str, remaining: float|None):
        step=steps[sid]; started_step=_now(); step_run_id=str(uuid4()); params=_render(step.get("parameters",{}),inputs,results)
        req=ActionRequest(app_id=step["app_id"],capability=step["capability"],parameters=params,reason=f"workflow {run['workflow_id']} step {sid}",actor=run["actor"],dry_run=dry_run,idempotency_key=f"workflow:{run_id}:{sid}")
        try:
            result=await asyncio.wait_for(execute_action(req),timeout=remaining) if remaining else await execute_action(req)
            result_data=result.model_dump(mode="json");status="success" if result.status in {"success","dry_run"} else result.status
        except asyncio.TimeoutError:
            status="failed";result_data={"status":"failed","message":"workflow step timeout"}
        except Exception as exc:
            status="failed";result_data={"status":"failed","message":str(exc)[:1000]}
        return sid,step,status,result_data,step_run_id,started_step

    while pending:
        live=get_run(run_id)
        if live and live["cancel_requested"]:
            final_status="cancelled";failure_message="cancel requested";break
        remaining=None
        if timeout:
            elapsed=(datetime.now(timezone.utc)-started).total_seconds();remaining=float(timeout)-elapsed
            if remaining<=0:final_status="failed";failure_message="workflow timeout exceeded";break
        ready=sorted([sid for sid in pending if all(statuses.get(dep)=="success" for dep in steps[sid].get("depends_on",[]))])
        blocked=[sid for sid in pending if any(statuses.get(dep) in {"failed","blocked","approval_required"} for dep in steps[sid].get("depends_on",[]))]
        for sid in blocked:statuses[sid]="blocked";pending.remove(sid)
        ready=[sid for sid in ready if sid in pending]
        if not ready:
            if pending:final_status="failed";failure_message="workflow cannot make progress"
            break
        batch=ready[:max_parallel]
        for sid in batch:pending.remove(sid)
        executed=await asyncio.gather(*(execute_step(sid,remaining) for sid in batch))
        stop_failure=None
        for sid,step,status,result_data,step_run_id,started_step in executed:
            statuses[sid]=status;results[sid]=result_data
            with connect() as conn:conn.execute("INSERT INTO workflow_step_runs(id,workflow_run_id,step_id,app_id,capability,status,result_json,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?)",(step_run_id,run_id,sid,step["app_id"],step["capability"],status,dumps(result_data),started_step,_now()))
            if status=="success":completed.append((step,result_data));continue
            if step.get("on_failure","stop")!="continue" and stop_failure is None:stop_failure=(sid,step,status,result_data)
        if stop_failure:
            sid,step,status,result_data=stop_failure
            final_status="waiting_approval" if status=="approval_required" else "failed";failure_message=result_data.get("message",status)
            if step.get("on_failure","stop")=="rollback" and not dry_run:
                rollback_results=[]
                for done_step,done_result in reversed(completed):
                    cap=registry.capability(done_step["app_id"],done_step["capability"])
                    if not cap or not cap.rollback_capability:continue
                    rr=await execute_action(ActionRequest(app_id=done_step["app_id"],capability=cap.rollback_capability,parameters={"workflow_run_id":run_id,"original":done_result},reason=f"workflow compensation for {sid}",actor=f"{run['actor']}:workflow-rollback",idempotency_key=f"workflow:{run_id}:rollback:{done_step['id']}"))
                    rollback_results.append(rr.model_dump(mode="json"))
                results["_rollback"]=rollback_results
            pending.clear();break
    if final_status=="success" and any(v not in {"success","blocked"} for v in statuses.values()):final_status="failed"
    final={"step_statuses":statuses,"step_results":results,"message":failure_message,"workflow_version":run["workflow_version"],"max_parallel_steps":max_parallel}
    with connect() as conn:conn.execute("UPDATE workflow_runs SET status=?,result_json=?,finished_at=? WHERE id=?",(final_status,dumps(final),_now(),run_id))
    audit(actor=run["actor"],app_id=None,capability=None,action="workflow.execute",decision=final_status,reason=run["reason"],request={"workflow_id":run["workflow_id"],"run_id":run_id,"version":run["workflow_version"]},result=final,organization_id=run["organization_id"])
    return get_run(run_id) or {"id":run_id,"status":final_status}

