from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.db import connect, dumps, loads
from app.models import InfrastructurePolicyCreate, PolicyLevel
from app.services.agents import enqueue_agent_task, get_agent
from app.services.audit import audit


def _now() -> str: return datetime.now(timezone.utc).isoformat()

# Safe baseline. Organization overrides may make a capability stricter or explicitly looser.
_DEFAULTS: dict[str, PolicyLevel] = {
    "system.status": PolicyLevel.READ,
    "system.processes": PolicyLevel.READ,
    "network.sockets": PolicyLevel.READ,
    "network.routes": PolicyLevel.READ,
    "firewall.status": PolicyLevel.READ,
    "package.updates": PolicyLevel.READ,
    "package.security_updates": PolicyLevel.READ,
    "package.apply_updates": PolicyLevel.APPROVAL,
    "service.status": PolicyLevel.READ,
    "service.logs": PolicyLevel.READ,
    "docker.list": PolicyLevel.READ,
    "docker.logs": PolicyLevel.READ,
    "repo.inspect": PolicyLevel.READ,
    "repo.context": PolicyLevel.READ,
    "repo.read": PolicyLevel.READ,
    "repo.search": PolicyLevel.READ,
    "service.restart": PolicyLevel.CONTROLLED,
    "docker.restart": PolicyLevel.CONTROLLED,
    "repo.apply_patch": PolicyLevel.CONTROLLED,
    "repo.run_steps": PolicyLevel.CONTROLLED,
    "repo.commit": PolicyLevel.CONTROLLED,
    "repo.prepare_release": PolicyLevel.CONTROLLED,
    "deploy.stage": PolicyLevel.CONTROLLED,
    "deploy.activate": PolicyLevel.CONTROLLED,
    "deploy.health": PolicyLevel.READ,
    "deploy.rollback": PolicyLevel.CONTROLLED,
    "deploy.cleanup": PolicyLevel.CONTROLLED,
    "service.start": PolicyLevel.APPROVAL,
    "service.stop": PolicyLevel.APPROVAL,
    "publish.nginx": PolicyLevel.APPROVAL,
    "deploy.configure": PolicyLevel.CONTROLLED,
}


def set_policy(item: InfrastructurePolicyCreate) -> dict[str, Any]:
    now=_now(); pid=str(uuid4())
    with connect() as conn:
        existing=conn.execute("SELECT id,created_at FROM infrastructure_policies WHERE organization_id=? AND capability=?",(item.organization_id,item.capability)).fetchone()
        if existing: pid=existing['id']; created=existing['created_at']
        else: created=now
        conn.execute("""INSERT INTO infrastructure_policies(id,organization_id,capability,level,constraints_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(organization_id,capability) DO UPDATE SET level=excluded.level,constraints_json=excluded.constraints_json,updated_at=excluded.updated_at""",
                     (pid,item.organization_id,item.capability,item.level.value,dumps(item.constraints),created,now))
    return get_policy(item.organization_id,item.capability)


def get_policy(organization_id: str, capability: str) -> dict[str, Any]:
    with connect() as conn: row=conn.execute("SELECT * FROM infrastructure_policies WHERE organization_id=? AND capability=?",(organization_id,capability)).fetchone()
    if row:
        item=dict(row); item['constraints']=loads(item.pop('constraints_json')); return item
    return {"id":None,"organization_id":organization_id,"capability":capability,"level":_DEFAULTS.get(capability,PolicyLevel.APPROVAL).value,"constraints":{},"default":True}


def list_policies(organization_id: str | None=None) -> list[dict[str,Any]]:
    with connect() as conn:
        rows=conn.execute("SELECT * FROM infrastructure_policies ORDER BY organization_id,capability").fetchall() if organization_id is None else conn.execute("SELECT * FROM infrastructure_policies WHERE organization_id=? ORDER BY capability",(organization_id,)).fetchall()
    out=[]
    for row in rows:
        item=dict(row); item['constraints']=loads(item.pop('constraints_json')); out.append(item)
    return out


def _constraints_ok(payload: dict[str,Any], constraints: dict[str,Any]) -> tuple[bool,str]:
    required=constraints.get('required_parameters',[]); missing=[x for x in required if x not in payload]
    if missing:return False,f"missing required parameters: {', '.join(missing)}"
    allowed=constraints.get('allowed_values',{})
    for key,values in allowed.items():
        if key in payload and payload[key] not in values:return False,f"parameter {key} is not allowed"
    return True,'ok'


def _create_approval(organization_id: str,agent_id: str,capability: str,actor: str,request:dict[str,Any]) -> str:
    aid=str(uuid4());now=_now()
    with connect() as conn: conn.execute("INSERT INTO infrastructure_approvals(id,organization_id,agent_id,capability,actor,status,request_json,approved_by,note,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,NULL,NULL,?,?)",(aid,organization_id,agent_id,capability,actor,dumps(request),now,now))
    return aid


def get_approval(approval_id: str) -> dict[str,Any] | None:
    with connect() as conn: row=conn.execute("SELECT * FROM infrastructure_approvals WHERE id=?",(approval_id,)).fetchone()
    if not row:return None
    item=dict(row);item['request']=loads(item.pop('request_json'));return item


def list_approvals(status: str|None=None,organization_id: str|None=None) -> list[dict[str,Any]]:
    where=[];params=[]
    if status:where.append('status=?');params.append(status)
    if organization_id is not None:where.append('organization_id=?');params.append(organization_id)
    sql='SELECT * FROM infrastructure_approvals'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY created_at DESC'
    with connect() as conn: rows=conn.execute(sql,tuple(params)).fetchall()
    out=[]
    for row in rows:
        item=dict(row);item['request']=loads(item.pop('request_json'));out.append(item)
    return out


def request_action(*,agent_id:str,capability:str,payload:dict[str,Any],organization_id:str,actor:str,reason:str,approval_granted:bool=False,correlation_type:str|None=None,correlation_id:str|None=None,correlation_phase:str|None=None) -> dict[str,Any]:
    agent=get_agent(agent_id)
    if not agent or agent['organization_id']!=organization_id:raise ValueError('agent not found in organization')
    if capability not in agent['capabilities']:raise ValueError('agent capability is not declared')
    policy=get_policy(organization_id,capability);level=PolicyLevel(policy['level'])
    ok,why=_constraints_ok(payload,policy.get('constraints') or {})
    if not ok:return {'status':'blocked','message':why,'capability':capability}
    if level==PolicyLevel.FORBIDDEN:
        audit(actor=actor,app_id=None,capability=capability,action='infrastructure.execute',decision='blocked',reason='infrastructure policy forbids action',request={'agent_id':agent_id,'payload':payload},organization_id=organization_id)
        return {'status':'blocked','message':'infrastructure action forbidden by policy','capability':capability}
    request={'agent_id':agent_id,'capability':capability,'payload':payload,'reason':reason,'correlation_type':correlation_type,'correlation_id':correlation_id,'correlation_phase':correlation_phase}
    if level==PolicyLevel.APPROVAL and not approval_granted:
        approval_id=_create_approval(organization_id,agent_id,capability,actor,request)
        audit(actor=actor,app_id=None,capability=capability,action='infrastructure.execute',decision='approval_required',reason=reason,request=request,result={'approval_id':approval_id},organization_id=organization_id)
        return {'status':'approval_required','approval_id':approval_id,'capability':capability}
    task=enqueue_agent_task(agent_id,capability,payload,organization_id=organization_id,correlation_type=correlation_type,correlation_id=correlation_id)
    audit(actor=actor,app_id=None,capability=capability,action='infrastructure.execute',decision='queued',reason=reason,request=request,result={'task_id':task['id'],'policy':level.value},organization_id=organization_id)
    return {'status':'queued','task':task,'capability':capability,'policy':level.value}


def approve(approval_id:str,approved_by:str) -> dict[str,Any]:
    item=get_approval(approval_id)
    if not item or item['status']!='pending':raise ValueError('infrastructure approval not pending')
    req=item['request']
    result=request_action(agent_id=item['agent_id'],capability=item['capability'],payload=dict(req.get('payload') or {}),organization_id=item['organization_id'],actor=f'{approved_by}-approved',reason=str(req.get('reason') or 'approved infrastructure action'),approval_granted=True,correlation_type=req.get('correlation_type'),correlation_id=req.get('correlation_id'),correlation_phase=req.get('correlation_phase'))
    with connect() as conn: conn.execute("UPDATE infrastructure_approvals SET status='approved',approved_by=?,updated_at=? WHERE id=?",(approved_by,_now(),approval_id))
    return result


def reject(approval_id:str,approved_by:str,note:str='') -> dict[str,Any]:
    item=get_approval(approval_id)
    if not item or item['status']!='pending':raise ValueError('infrastructure approval not pending')
    with connect() as conn: conn.execute("UPDATE infrastructure_approvals SET status='rejected',approved_by=?,note=?,updated_at=? WHERE id=?",(approved_by,note,_now(),approval_id))
    return {'status':'rejected','approval_id':approval_id}
