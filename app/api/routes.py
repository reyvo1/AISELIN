from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.connectors.factory import build_connector
from app.connectors.registry import available_connectors
from app.ai.router import provider_status
from app.core.security import ActorContext, AgentContext, ensure_org_scope, require_agent, require_event_source, require_owner, require_permission
from app.core.migrations import schema_status
from app.core.config import settings
from app.models import (ActionRequest, ApiKeyCreate, ApplicationManifest, CommandRequest, EventIn, IncidentResolve,
                        BusinessCreate, LocationCreate, NotificationChannelCreate, OrganizationCreate, PolicyCreate, PrincipalCreate, QueueAction, ResourceCreate, ResourceEdgeCreate, RuleCreate, WorkflowCreate, WorkflowRunRequest, MemoryCreate, KnowledgeDocumentCreate, AgentNodeCreate, AgentHeartbeat, AgentTaskResult, AgentTaskCreate, ProjectProfileCreate, DeploymentTargetCreate, DeploymentRequest, OperatorCommandRequest, DeveloperSessionRequest, ServerSessionRequest, InfrastructurePolicyCreate, InfrastructureActionRequest)
from app.services.approvals import get_approval, list_approvals, resolve_approval
from app.services.audit import recent
from app.services.executor import execute_action
from app.services.identity import (create_api_key, create_business, create_location, create_organization, create_principal,
                                   list_businesses, list_locations, list_organizations, revoke_api_key)
from app.services.incidents import list_incidents, resolve_incident
from app.services.jobs import enqueue, get_job, list_jobs, retry_job, cancel_job
from app.services.lifecycle import get_state, validate
from app.services.notifications import create_channel, get_channel, list_channels
from app.services.planner import plan
from app.services.operations import summary as operations_summary
from app.services.policy import policy_service
from app.services.registry import registry
from app.services.resources import add_edge, impact, list_resources, upsert_resource
from app.services.rules import create_rule, ingest_event, list_rules, run_due_schedules
from app.services.vault import put_secret, rotate_webhook_secret, put_deployment_secret, get_deployment_secret
from app.services.webhooks import verify_signed_event
from app.services.workflows import create_workflow, update_workflow, set_workflow_enabled, create_run, get_run, get_workflow, list_runs, list_workflows, cancel_run, prepare_resume
from app.services.memory import upsert_memory, list_memory
from app.services.knowledge import create_document, get_document, delete_document, list_documents, search_documents
from app.services.telemetry import counters as telemetry_counters
from app.services.snapshots import snapshot_manifest
from app.services.integrity import verify_integrity
from app.services.workers import list_workers
from app.services.slo import current_slo
from app.services.agents import create_agent, rotate_agent_token, get_agent, list_agents, heartbeat as agent_heartbeat, enqueue_agent_task, get_agent_task, list_agent_tasks, claim_agent_task, complete_agent_task, attach_artifact
from app.services.artifacts import store_artifact, get_artifact, list_artifacts, read_artifact_bytes, prune_artifacts
from app.services.projects import create_project, get_project, list_projects, create_target, get_target, list_targets, set_project_enabled, set_target_enabled
from app.services.deployments import start_deployment, get_deployment, list_deployments, list_releases, advance_from_agent_task
from app.services.operator import execute_operator, get_operator_run, list_operator_runs, tool_catalog
from app.services.developer import start_session as start_developer_session, get_session as get_developer_session, list_sessions as list_developer_sessions, advance_from_agent_task as advance_developer_from_agent_task
from app.services.server_ops import start_session as start_server_session, get_session as get_server_session, list_sessions as list_server_sessions, advance_from_agent_task as advance_server_from_agent_task
from app.services.infrastructure import set_policy as set_infra_policy, list_policies as list_infra_policies, request_action as request_infra_action, get_approval as get_infra_approval, list_approvals as list_infra_approvals, approve as approve_infra, reject as reject_infra
from fastapi.responses import PlainTextResponse, StreamingResponse, Response
import asyncio

router = APIRouter(prefix="/api/v1")


def _app_scope(ctx: ActorContext, app_id: str) -> ApplicationManifest:
    manifest=registry.get(app_id)
    if not manifest: raise HTTPException(404,"application not found")
    ensure_org_scope(ctx,manifest.organization_id)
    return manifest



@router.get("/system/info", dependencies=[Depends(require_owner)])
async def system_info():
    db_backend="postgresql" if settings.database_url.startswith("postgresql") else "sqlite"
    return {"service":"AIOC","version":"0.9.0-alpha.1","environment":settings.env,"database_backend":db_backend,"schema":schema_status()}

@router.get("/operations/summary")
async def operation_summary(ctx: ActorContext=Depends(require_permission("applications:read"))):
    return operations_summary(ctx.organization_id)

@router.get("/system/snapshot-manifest", dependencies=[Depends(require_owner)])
async def system_snapshot_manifest(): return snapshot_manifest()

@router.get("/system/integrity", dependencies=[Depends(require_owner)])
async def system_integrity(): return verify_integrity()

@router.get("/health")
async def health(): return {"ok":True,"service":"ai-autonomous-ops-center","api_version":"v1"}

@router.post("/organizations", dependencies=[Depends(require_owner)])
async def organizations_create(item: OrganizationCreate): return create_organization(item)

@router.get("/organizations", dependencies=[Depends(require_owner)])
async def organizations_list(): return list_organizations()


@router.post("/businesses")
async def business_create(item: BusinessCreate, ctx: ActorContext=Depends(require_permission("org:write"))):
    ensure_org_scope(ctx,item.organization_id)
    try: return create_business(item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/businesses")
async def businesses_list(ctx: ActorContext=Depends(require_permission("org:read"))):
    return list_businesses(ctx.organization_id)

@router.post("/locations")
async def location_create(item: LocationCreate, ctx: ActorContext=Depends(require_permission("org:write"))):
    ensure_org_scope(ctx,item.organization_id)
    try: return create_location(item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/locations")
async def locations_list(ctx: ActorContext=Depends(require_permission("org:read"))):
    return list_locations(ctx.organization_id)

@router.post("/notification-channels")
async def notification_channel_create(item: NotificationChannelCreate, ctx: ActorContext=Depends(require_permission("notifications:write"))):
    ensure_org_scope(ctx,item.organization_id)
    try: return create_channel(item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/notification-channels")
async def notification_channels(ctx: ActorContext=Depends(require_permission("notifications:read"))):
    return list_channels(ctx.organization_id)

@router.post("/notification-channels/{channel_id}/secrets/{name}")
async def notification_channel_secret(channel_id: str, name: str, value: str=Body(embed=True), ctx: ActorContext=Depends(require_permission("notifications:write"))):
    channel=get_channel(channel_id)
    if not channel: raise HTTPException(404,"notification channel not found")
    ensure_org_scope(ctx,channel["organization_id"])
    return put_secret(f"notification:{channel_id}",name,value)

@router.post("/principals", dependencies=[Depends(require_owner)])
async def principals_create(item: PrincipalCreate): return create_principal(item)

@router.post("/api-keys", dependencies=[Depends(require_owner)])
async def api_key_create(item: ApiKeyCreate):
    try: return create_api_key(item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.delete("/api-keys/{key_id}", dependencies=[Depends(require_owner)])
async def api_key_revoke(key_id: str):
    if not revoke_api_key(key_id): raise HTTPException(404,"api key not found")
    return {"revoked":True,"id":key_id}


@router.get("/ai/providers")
async def ai_providers(ctx: ActorContext=Depends(require_permission("ai:read"))):
    return provider_status()

@router.get("/connectors")
async def connectors(ctx: ActorContext=Depends(require_permission("applications:read"))):
    return {"available":available_connectors()}

@router.get("/applications")
async def applications(ctx: ActorContext=Depends(require_permission("applications:read"))):
    return [x.model_dump(mode="json") for x in registry.list(ctx.organization_id)]

@router.post("/applications")
async def register_application(manifest: ApplicationManifest, ctx: ActorContext=Depends(require_permission("applications:write"))):
    ensure_org_scope(ctx,manifest.organization_id)
    try: return registry.register(manifest).model_dump(mode="json")
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.delete("/applications/{app_id}")
async def disable_application(app_id: str, ctx: ActorContext=Depends(require_permission("applications:write"))):
    _app_scope(ctx,app_id)
    if not registry.disable(app_id): raise HTTPException(404,"application not found")
    return {"disabled":True,"app_id":app_id}

@router.get("/applications/{app_id}/health")
async def application_health(app_id: str, ctx: ActorContext=Depends(require_permission("applications:read"))):
    manifest=_app_scope(ctx,app_id); return await build_connector(manifest).health()

@router.get("/applications/{app_id}/discover")
async def application_discover(app_id: str, ctx: ActorContext=Depends(require_permission("applications:read"))):
    manifest=_app_scope(ctx,app_id); return await build_connector(manifest).discover()

@router.post("/applications/{app_id}/validate")
async def application_validate(app_id: str, ctx: ActorContext=Depends(require_permission("applications:write"))):
    _app_scope(ctx,app_id); return await validate(app_id)

@router.get("/applications/{app_id}/lifecycle")
async def application_lifecycle(app_id: str, ctx: ActorContext=Depends(require_permission("applications:read"))):
    _app_scope(ctx,app_id); return get_state(app_id)

@router.post("/applications/{app_id}/webhook-secret/rotate")
async def webhook_secret_rotate(app_id: str, ctx: ActorContext=Depends(require_permission("applications:write"))):
    _app_scope(ctx,app_id); return rotate_webhook_secret(app_id)

@router.post("/applications/{app_id}/connector-secrets/{name}", dependencies=[Depends(require_owner)])
async def connector_secret_put(app_id: str, name: str, value: str=Body(embed=True)):
    if not registry.get(app_id): raise HTTPException(404,"application not found")
    return put_secret(f"connector:{app_id}",name,value)

@router.get("/global/health")
async def global_health(ctx: ActorContext=Depends(require_permission("applications:read"))):
    out=[]
    for manifest in registry.list(ctx.organization_id):
        health_data=await build_connector(manifest).health(); out.append({"id":manifest.id,"name":manifest.name,"type":manifest.type,"organization_id":manifest.organization_id,"health":health_data})
    return {"total":len(out),"healthy":sum(1 for x in out if x["health"].get("ok")),"unhealthy":sum(1 for x in out if not x["health"].get("ok")),"applications":out}

@router.post("/policies")
async def set_policy(item: PolicyCreate, ctx: ActorContext=Depends(require_permission("policies:write"))):
    if item.app_id: _app_scope(ctx,item.app_id)
    elif ctx.organization_id is not None: raise HTTPException(403,"global policies require owner scope")
    return policy_service.upsert(item)

@router.get("/policies")
async def policies(ctx: ActorContext=Depends(require_permission("policies:read"))):
    rows=policy_service.list()
    if ctx.organization_id is None: return rows
    allowed={m.id for m in registry.list(ctx.organization_id)}
    return [r for r in rows if r["app_id"] in allowed]

@router.post("/actions/execute")
async def execute(req: ActionRequest, ctx: ActorContext=Depends(require_permission("actions:execute"))):
    _app_scope(ctx,req.app_id); req.actor=ctx.actor_id
    return (await execute_action(req)).model_dump(mode="json")

@router.post("/actions/queue")
async def queue_action(item: QueueAction, ctx: ActorContext=Depends(require_permission("actions:execute"))):
    _app_scope(ctx,item.request.app_id); item.request.actor=ctx.actor_id
    try: job=enqueue("action",{"request":item.request.model_dump(mode="json")},max_attempts=item.max_attempts)
    except RuntimeError as exc: raise HTTPException(429,str(exc)) from exc
    return {"status":"queued","job":job}

@router.post("/commands")
async def command(req: CommandRequest, ctx: ActorContext=Depends(require_permission("actions:execute"))):
    lowered=req.command.lower().strip()
    if any(x in lowered for x in ["cek semua","status semua","kondisi semua","global health"]): return {"mode":"global_health","result":await global_health(ctx)}
    try: actions=await plan(req.command)
    except Exception as exc: raise HTTPException(400,str(exc)) from exc
    results=[]
    for action in actions:
        _app_scope(ctx,action.app_id)
        result=await execute_action(ActionRequest(app_id=action.app_id,capability=action.capability,parameters=action.parameters,reason=action.rationale or req.command,actor=ctx.actor_id,dry_run=req.dry_run))
        results.append({"plan":action.model_dump(mode="json"),"result":result.model_dump(mode="json")})
    return {"mode":"planned_actions","results":results}

@router.get("/approvals")
async def approvals(status: str|None=None, ctx: ActorContext=Depends(require_permission("approvals:read"))):
    return list_approvals(status,ctx.organization_id)

@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str, ctx: ActorContext=Depends(require_permission("approvals:resolve"))):
    item=get_approval(approval_id)
    if not item: raise HTTPException(404,"approval not found")
    _app_scope(ctx,item["app_id"])
    if item["status"] != "pending": raise HTTPException(409,"approval already resolved")
    if not resolve_approval(approval_id,"approved",ctx.actor_id): raise HTTPException(409,"approval resolution failed")
    req=ActionRequest.model_validate(item["request"]); req.actor=f"{ctx.actor_id}-approved"; result=await execute_action(req,approval_granted=True)
    resumed=None
    if result.status in {"success","dry_run"} and req.idempotency_key and req.idempotency_key.startswith("workflow:"):
        parts=req.idempotency_key.split(":")
        if len(parts) >= 3:
            run_id=parts[1]; run=get_run(run_id)
            if run:
                resumed=enqueue("workflow",{"run_id":run_id,"organization_id":run["organization_id"]},organization_id=run["organization_id"])
    return {"approval_id":approval_id,"result":result.model_dump(mode="json"),"workflow_resume_job":resumed}

@router.post("/approvals/{approval_id}/reject")
async def reject(approval_id: str, ctx: ActorContext=Depends(require_permission("approvals:resolve"))):
    item=get_approval(approval_id)
    if not item: raise HTTPException(404,"approval not found")
    _app_scope(ctx,item["app_id"])
    if not resolve_approval(approval_id,"rejected",ctx.actor_id): raise HTTPException(409,"approval already resolved")
    return {"approval_id":approval_id,"status":"rejected"}

@router.get("/audit")
async def audit_events(limit: int=100, ctx: ActorContext=Depends(require_permission("audit:read"))):
    return recent(limit,ctx.organization_id)

@router.post("/rules")
async def add_rule(rule: RuleCreate, ctx: ActorContext=Depends(require_permission("rules:write"))):
    action=rule.action
    if "app_id" not in action or "capability" not in action: raise HTTPException(400,"rule action needs app_id and capability")
    _app_scope(ctx,action["app_id"])
    if not registry.capability_exists(action["app_id"],action["capability"]): raise HTTPException(400,"rule action app/capability is not registered")
    return create_rule(rule)

@router.get("/rules")
async def rules(ctx: ActorContext=Depends(require_permission("rules:read"))):
    return list_rules(ctx.organization_id)

@router.post("/events", dependencies=[Depends(require_event_source)])
async def events(event: EventIn):
    if settings.production:
        raise HTTPException(410,"legacy shared-token event endpoint is disabled in production; use signed per-application webhook")
    return await ingest_event(event)

@router.post("/events/webhook/{app_id}")
async def signed_events(app_id: str, request: Request):
    manifest=registry.get(app_id)
    if not manifest: raise HTTPException(404,"application not found")
    body=await request.body(); ok,reason=verify_signed_event(app_id,body,request.headers.get("X-AIOC-Timestamp"),request.headers.get("X-AIOC-Signature"))
    if not ok: raise HTTPException(401,reason)
    try: payload=json.loads(body)
    except ValueError as exc: raise HTTPException(400,"invalid JSON") from exc
    event=EventIn.model_validate(payload); event.source_app_id=app_id
    return await ingest_event(event)

@router.post("/scheduler/run-once")
async def scheduler_run_once(ctx: ActorContext=Depends(require_permission("rules:write"))): return {"fired":await run_due_schedules(f"api-{ctx.actor_id}")}

@router.get("/jobs")
async def jobs(status: str|None=None, limit: int=100, ctx: ActorContext=Depends(require_permission("jobs:read"))):
    return list_jobs(status,limit,ctx.organization_id)

@router.get("/jobs/{job_id}")
async def job_get(job_id: str, ctx: ActorContext=Depends(require_permission("jobs:read"))):
    job=get_job(job_id)
    if not job: raise HTTPException(404,"job not found")
    ensure_org_scope(ctx,job.get("organization_id","global"))
    return job

@router.post("/jobs/{job_id}/retry")
async def job_retry(job_id: str, ctx: ActorContext=Depends(require_permission("jobs:write"))):
    job=get_job(job_id)
    if not job: raise HTTPException(404,"job not found")
    ensure_org_scope(ctx,job.get("organization_id","global"))
    updated=retry_job(job_id)
    if not updated: raise HTTPException(409,"job is not retryable")
    return updated

@router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str, ctx: ActorContext=Depends(require_permission("jobs:write"))):
    job=get_job(job_id)
    if not job: raise HTTPException(404,"job not found")
    ensure_org_scope(ctx,job.get("organization_id","global"))
    updated=cancel_job(job_id)
    if not updated: raise HTTPException(409,"job is not cancellable")
    return updated

@router.get("/incidents")
async def incidents(status: str|None=None, ctx: ActorContext=Depends(require_permission("incidents:read"))):
    return list_incidents(status,ctx.organization_id)

@router.post("/incidents/{incident_id}/resolve")
async def incident_resolve(incident_id: str, item: IncidentResolve, ctx: ActorContext=Depends(require_permission("incidents:resolve"))):
    candidates=[i for i in list_incidents(None,ctx.organization_id) if i["id"]==incident_id]
    if not candidates: raise HTTPException(404,"incident not found")
    if not resolve_incident(incident_id,item.note): raise HTTPException(409,"incident already resolved")
    return {"id":incident_id,"status":"resolved"}

@router.post("/resources")
async def resource_upsert(item: ResourceCreate, ctx: ActorContext=Depends(require_permission("resources:write"))):
    ensure_org_scope(ctx,item.organization_id)
    if item.app_id: _app_scope(ctx,item.app_id)
    return upsert_resource(item)

@router.get("/resources")
async def resources(ctx: ActorContext=Depends(require_permission("resources:read"))): return list_resources(ctx.organization_id)

@router.post("/resources/edges")
async def resource_edge(item: ResourceEdgeCreate, ctx: ActorContext=Depends(require_permission("resources:write"))):
    ensure_org_scope(ctx,item.organization_id)
    try: return add_edge(item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/resources/{resource_id}/impact")
async def resource_impact(resource_id: str, depth: int=4, ctx: ActorContext=Depends(require_permission("resources:read"))):
    graph=impact(resource_id,depth)
    if not graph["resources"]: raise HTTPException(404,"resource not found")
    ensure_org_scope(ctx,graph["resources"][0]["organization_id"]); return graph


@router.post("/workflows")
async def workflow_create(item: WorkflowCreate, ctx: ActorContext=Depends(require_permission("rules:write"))):
    ensure_org_scope(ctx,item.organization_id)
    try: return create_workflow(item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.put("/workflows/{workflow_id}")
async def workflow_update(workflow_id: str,item: WorkflowCreate,ctx: ActorContext=Depends(require_permission("rules:write"))):
    wf=get_workflow(workflow_id)
    if not wf: raise HTTPException(404,"workflow not found")
    ensure_org_scope(ctx,wf["organization_id"])
    try: return update_workflow(workflow_id,item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.post("/workflows/{workflow_id}/enable")
async def workflow_enable(workflow_id: str,ctx: ActorContext=Depends(require_permission("rules:write"))):
    wf=get_workflow(workflow_id)
    if not wf: raise HTTPException(404,"workflow not found")
    ensure_org_scope(ctx,wf["organization_id"]); return set_workflow_enabled(workflow_id,True)

@router.post("/workflows/{workflow_id}/disable")
async def workflow_disable(workflow_id: str,ctx: ActorContext=Depends(require_permission("rules:write"))):
    wf=get_workflow(workflow_id)
    if not wf: raise HTTPException(404,"workflow not found")
    ensure_org_scope(ctx,wf["organization_id"]); return set_workflow_enabled(workflow_id,False)

@router.get("/workflows")
async def workflows(ctx: ActorContext=Depends(require_permission("rules:read"))):
    return list_workflows(ctx.organization_id)

@router.get("/workflows/runs")
async def workflow_runs(limit: int=100, ctx: ActorContext=Depends(require_permission("jobs:read"))):
    return list_runs(ctx.organization_id,limit)

@router.get("/workflows/{workflow_id}")
async def workflow_get(workflow_id: str, ctx: ActorContext=Depends(require_permission("rules:read"))):
    wf=get_workflow(workflow_id)
    if not wf: raise HTTPException(404,"workflow not found")
    ensure_org_scope(ctx,wf["organization_id"]); return wf

@router.post("/workflows/{workflow_id}/run")
async def workflow_run(workflow_id: str, item: WorkflowRunRequest, ctx: ActorContext=Depends(require_permission("actions:execute"))):
    wf=get_workflow(workflow_id)
    if not wf: raise HTTPException(404,"workflow not found")
    ensure_org_scope(ctx,wf["organization_id"])
    try: run=create_run(workflow_id,ctx.actor_id,item.reason,item.inputs,item.dry_run,item.timeout_seconds)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc
    try: job=enqueue("workflow",{"run_id":run["id"],"organization_id":wf["organization_id"]},organization_id=wf["organization_id"])
    except RuntimeError as exc:
        cancel_run(run["id"]); raise HTTPException(429,str(exc)) from exc
    return {"status":"queued","run":run,"job":job}

@router.get("/workflow-runs/{run_id}")
async def workflow_run_get(run_id: str, ctx: ActorContext=Depends(require_permission("jobs:read"))):
    run=get_run(run_id)
    if not run: raise HTTPException(404,"workflow run not found")
    ensure_org_scope(ctx,run["organization_id"]); return run

@router.post("/memory")
async def memory_put(item: MemoryCreate, ctx: ActorContext=Depends(require_permission("resources:write"))):
    ensure_org_scope(ctx,item.organization_id)
    if item.app_id: _app_scope(ctx,item.app_id)
    return upsert_memory(item)

@router.get("/memory")
async def memory_list(organization_id: str|None=None, app_id: str|None=None, namespace: str|None=None, limit: int=200, ctx: ActorContext=Depends(require_permission("resources:read"))):
    manifest=_app_scope(ctx,app_id) if app_id else None
    org=ctx.organization_id or organization_id or (manifest.organization_id if manifest else "global")
    ensure_org_scope(ctx,org)
    if manifest and manifest.organization_id != org: raise HTTPException(400,"application does not belong to requested organization")
    return list_memory(org,app_id,namespace,limit)

@router.post("/knowledge/documents")
async def knowledge_create(item: KnowledgeDocumentCreate, ctx: ActorContext=Depends(require_permission("resources:write"))):
    ensure_org_scope(ctx,item.organization_id)
    if item.app_id: _app_scope(ctx,item.app_id)
    return create_document(item)

@router.get("/knowledge/documents")
async def knowledge_list(app_id: str|None=None, limit: int=100, ctx: ActorContext=Depends(require_permission("resources:read"))):
    if app_id: _app_scope(ctx,app_id)
    org=ctx.organization_id or "global"
    return list_documents(org,app_id,limit)

@router.get("/knowledge/search")
async def knowledge_search(q: str, app_id: str|None=None, limit: int=10, ctx: ActorContext=Depends(require_permission("resources:read"))):
    if app_id: _app_scope(ctx,app_id)
    org=ctx.organization_id or "global"
    return {"query":q,"results":search_documents(org,q,app_id,limit)}

@router.delete("/knowledge/documents/{document_id}")
async def knowledge_delete(document_id: str, ctx: ActorContext=Depends(require_permission("resources:write"))):
    doc=get_document(document_id)
    if not doc: raise HTTPException(404,"knowledge document not found")
    ensure_org_scope(ctx,doc["organization_id"])
    return {"deleted":delete_document(document_id)}

@router.get("/telemetry/counters")
async def telemetry(ctx: ActorContext=Depends(require_permission("audit:read"))):
    return telemetry_counters()

@router.get("/telemetry/slo")
async def slo(ctx: ActorContext=Depends(require_permission("audit:read"))): return current_slo()




@router.post("/workflow-runs/{run_id}/cancel")
async def workflow_run_cancel(run_id: str,ctx: ActorContext=Depends(require_permission("jobs:write"))):
    run=get_run(run_id)
    if not run: raise HTTPException(404,"workflow run not found")
    ensure_org_scope(ctx,run["organization_id"]); return cancel_run(run_id)

@router.post("/workflow-runs/{run_id}/resume")
async def workflow_run_resume(run_id: str,ctx: ActorContext=Depends(require_permission("jobs:write"))):
    run=get_run(run_id)
    if not run: raise HTTPException(404,"workflow run not found")
    ensure_org_scope(ctx,run["organization_id"])
    try: resumed=prepare_resume(run_id)
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc
    job=enqueue("workflow",{"run_id":run_id,"organization_id":run["organization_id"]},organization_id=run["organization_id"]); return {"run":resumed,"job":job}

@router.get("/workers", dependencies=[Depends(require_owner)])
async def workers(): return list_workers()

@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(ctx: ActorContext=Depends(require_permission("audit:read"))):
    values=telemetry_counters()
    lines=["# TYPE aioc_counter counter"]
    for key,value in values.items():
        metric="aioc_"+"".join(c if c.isalnum() else "_" for c in key)
        lines.append(f"{metric} {value}")
    op=operations_summary(ctx.organization_id)
    for key,value in op.items():
        if isinstance(value,(int,float)):
            lines.append(f"aioc_operations_{key} {value}")
    return "\n".join(lines)+"\n"

@router.get("/stream/operations")
async def operations_stream(ctx: ActorContext=Depends(require_permission("applications:read"))):
    async def generate():
        previous=None
        while True:
            payload=json.dumps(operations_summary(ctx.organization_id),separators=(",",":"))
            if payload != previous:
                yield f"event: operations\ndata: {payload}\n\n"
                previous=payload
            await asyncio.sleep(2)
    return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@router.post("/agents")
async def agent_create(item: AgentNodeCreate, ctx: ActorContext=Depends(require_permission("agents:write"))):
    ensure_org_scope(ctx,item.organization_id)
    try: return create_agent(item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/agents")
async def agents(ctx: ActorContext=Depends(require_permission("agents:read"))):
    return list_agents(ctx.organization_id)

@router.post("/agents/{agent_id}/rotate-token")
async def agent_rotate(agent_id: str, ctx: ActorContext=Depends(require_permission("agents:write"))):
    agent=get_agent(agent_id)
    if not agent: raise HTTPException(404,"agent not found")
    ensure_org_scope(ctx,agent["organization_id"])
    result=rotate_agent_token(agent_id)
    if not result: raise HTTPException(404,"agent not found")
    return result


@router.post("/organizations/{organization_id}/deployment-secrets/{name}")
async def deployment_secret_put(organization_id: str, name: str, value: str=Body(embed=True), ctx: ActorContext=Depends(require_permission("deployments:write"))):
    ensure_org_scope(ctx, organization_id)
    try:
        return put_deployment_secret(organization_id, name, value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

@router.get("/agent/secrets/{name}")
async def agent_deployment_secret(name: str, agent: AgentContext=Depends(require_agent)):
    node=get_agent(agent.agent_id)
    if not node:
        raise HTTPException(404,"agent not found")
    allowed=set(node.get("metadata",{}).get("allowed_secrets") or [])
    if name not in allowed:
        raise HTTPException(403,"deployment secret is not granted to this agent")
    value=get_deployment_secret(agent.organization_id,name)
    if value is None:
        raise HTTPException(404,"deployment secret not found")
    return {"name":name,"value":value}

@router.post("/agent-tasks")
async def agent_task_create(item: AgentTaskCreate, ctx: ActorContext=Depends(require_permission("deployments:write"))):
    agent=get_agent(item.agent_id)
    if not agent: raise HTTPException(404,"agent not found")
    ensure_org_scope(ctx,agent["organization_id"])
    try: return enqueue_agent_task(item.agent_id,item.capability,item.payload,organization_id=agent["organization_id"],max_attempts=item.max_attempts)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/agent-tasks")
async def agent_tasks(agent_id: str|None=None, status: str|None=None, limit: int=100, ctx: ActorContext=Depends(require_permission("deployments:read"))):
    if agent_id:
        agent=get_agent(agent_id)
        if not agent: raise HTTPException(404,"agent not found")
        ensure_org_scope(ctx,agent["organization_id"])
    return list_agent_tasks(ctx.organization_id,agent_id,status,limit)

@router.post("/agent/heartbeat")
async def agent_self_heartbeat(item: AgentHeartbeat, agent: AgentContext=Depends(require_agent)):
    try: return agent_heartbeat(agent.agent_id,item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.post("/agent/tasks/claim")
async def agent_task_claim(agent: AgentContext=Depends(require_agent)):
    return {"task":claim_agent_task(agent.agent_id)}

@router.post("/agent/tasks/{task_id}/artifact")
async def agent_task_artifact(task_id: str, request: Request, agent: AgentContext=Depends(require_agent)):
    task=get_agent_task(task_id)
    if not task or task["agent_id"]!=agent.agent_id: raise HTTPException(404,"agent task not found")
    if task["status"]!="running": raise HTTPException(409,"agent task is not running")
    content=await request.body()
    expected=request.headers.get("x-artifact-sha256")
    filename=request.headers.get("x-artifact-filename") or "release.tar.gz"
    project_id=request.headers.get("x-project-id") or task.get("payload",{}).get("project_id")
    try:
        artifact=store_artifact(organization_id=agent.organization_id,project_id=project_id,kind="release",filename=filename,content=content,expected_sha256=expected,metadata={"task_id":task_id,"agent_id":agent.agent_id})
        attach_artifact(task_id,artifact["id"]); return artifact
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/agent/artifacts/{artifact_id}")
async def agent_artifact_download(artifact_id: str, agent: AgentContext=Depends(require_agent)):
    artifact=get_artifact(artifact_id)
    if not artifact or artifact["organization_id"]!=agent.organization_id: raise HTTPException(404,"artifact not found")
    try: content=read_artifact_bytes(artifact_id)
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc
    return Response(content=content,media_type="application/octet-stream",headers={"X-Artifact-SHA256":artifact["sha256"],"Content-Disposition":f'attachment; filename="{artifact["filename"]}"'})

@router.post("/agent/tasks/{task_id}/complete")
async def agent_task_complete(task_id: str, item: AgentTaskResult, agent: AgentContext=Depends(require_agent)):
    try: task=complete_agent_task(agent.agent_id,task_id,item.status,item.result,item.error)
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc
    deployment=advance_from_agent_task(task_id) if task["status"] in {"success","failed"} else None
    developer=await advance_developer_from_agent_task(task_id) if task["status"] in {"success","failed"} else None
    server_session=await advance_server_from_agent_task(task_id) if task["status"] in {"success","failed"} else None
    return {"task":task,"deployment":deployment,"developer":developer,"server_session":server_session}

@router.post("/infrastructure/policies")
async def infrastructure_policy_set(item: InfrastructurePolicyCreate, ctx: ActorContext=Depends(require_permission("policies:write"))):
    ensure_org_scope(ctx,item.organization_id); return set_infra_policy(item)

@router.get("/infrastructure/policies")
async def infrastructure_policies(ctx: ActorContext=Depends(require_permission("policies:read"))):
    return list_infra_policies(ctx.organization_id)

@router.post("/infrastructure/actions")
async def infrastructure_action(item: InfrastructureActionRequest, ctx: ActorContext=Depends(require_permission("deployments:write"))):
    agent=get_agent(item.agent_id)
    if not agent: raise HTTPException(404,"agent not found")
    ensure_org_scope(ctx,agent["organization_id"])
    try: return request_infra_action(agent_id=item.agent_id,capability=item.capability,payload=item.payload,organization_id=agent["organization_id"],actor=ctx.actor_id,reason=item.reason)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/infrastructure/approvals")
async def infrastructure_approvals(status: str|None=None, ctx: ActorContext=Depends(require_permission("approvals:read"))):
    return list_infra_approvals(status,ctx.organization_id)

@router.post("/infrastructure/approvals/{approval_id}/approve")
async def infrastructure_approval_approve(approval_id: str, ctx: ActorContext=Depends(require_permission("approvals:resolve"))):
    item=get_infra_approval(approval_id)
    if not item: raise HTTPException(404,"infrastructure approval not found")
    ensure_org_scope(ctx,item["organization_id"])
    try:
        result=approve_infra(approval_id,ctx.actor_id)
        if item["request"].get("correlation_type")=="server_session" and result.get("task"):
            from app.services.server_ops import attach_approved_task
            attach_approved_task(str(item["request"].get("correlation_id")),result["task"]["id"],str(item["request"].get("correlation_phase") or item["capability"]))
        return {"approval_id":approval_id,"result":result}
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc

@router.post("/infrastructure/approvals/{approval_id}/reject")
async def infrastructure_approval_reject(approval_id: str, ctx: ActorContext=Depends(require_permission("approvals:resolve"))):
    item=get_infra_approval(approval_id)
    if not item: raise HTTPException(404,"infrastructure approval not found")
    ensure_org_scope(ctx,item["organization_id"])
    try:
        result=reject_infra(approval_id,ctx.actor_id)
        if item["request"].get("correlation_type")=="server_session" and item["request"].get("correlation_id"):
            from app.services.server_ops import reject_waiting_approval
            reject_waiting_approval(str(item["request"].get("correlation_id")), "infrastructure approval rejected")
        return result
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc


@router.post("/server/sessions")
async def server_session_create(item: ServerSessionRequest, ctx: ActorContext=Depends(require_permission("deployments:write"))):
    agent=get_agent(item.agent_id)
    if not agent: raise HTTPException(404,"server agent not found")
    ensure_org_scope(ctx,agent["organization_id"])
    try: return start_server_session(item,ctx.actor_id,agent["organization_id"])
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/server/sessions")
async def server_sessions(agent_id: str|None=None, ctx: ActorContext=Depends(require_permission("deployments:read"))):
    if agent_id:
        agent=get_agent(agent_id)
        if not agent: raise HTTPException(404,"server agent not found")
        ensure_org_scope(ctx,agent["organization_id"])
    return list_server_sessions(ctx.organization_id,agent_id)

@router.get("/server/sessions/{session_id}")
async def server_session_get(session_id: str, ctx: ActorContext=Depends(require_permission("deployments:read"))):
    item=get_server_session(session_id)
    if not item: raise HTTPException(404,"server session not found")
    ensure_org_scope(ctx,item["organization_id"]); return item


@router.post("/projects")
async def project_create(item: ProjectProfileCreate, ctx: ActorContext=Depends(require_permission("projects:write"))):
    ensure_org_scope(ctx,item.organization_id)
    try: return create_project(item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/projects")
async def projects(ctx: ActorContext=Depends(require_permission("projects:read"))): return list_projects(ctx.organization_id)

@router.post("/projects/{project_id}/enabled")
async def project_enabled(project_id: str, enabled: bool=Body(embed=True), ctx: ActorContext=Depends(require_permission("projects:write"))):
    project=get_project(project_id)
    if not project: raise HTTPException(404,"project not found")
    ensure_org_scope(ctx,project["organization_id"])
    return set_project_enabled(project_id,enabled)

@router.get("/projects/{project_id}")
async def project_get(project_id: str, ctx: ActorContext=Depends(require_permission("projects:read"))):
    project=get_project(project_id)
    if not project: raise HTTPException(404,"project not found")
    ensure_org_scope(ctx,project["organization_id"]); return project

@router.post("/deployment-targets")
async def deployment_target_create(item: DeploymentTargetCreate, ctx: ActorContext=Depends(require_permission("projects:write"))):
    ensure_org_scope(ctx,item.organization_id)
    try: return create_target(item)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/deployment-targets")
async def deployment_targets(project_id: str|None=None, ctx: ActorContext=Depends(require_permission("projects:read"))):
    return list_targets(project_id,ctx.organization_id)

@router.post("/deployment-targets/{target_id}/enabled")
async def deployment_target_enabled(target_id: str, enabled: bool=Body(embed=True), ctx: ActorContext=Depends(require_permission("projects:write"))):
    target=get_target(target_id)
    if not target: raise HTTPException(404,"deployment target not found")
    ensure_org_scope(ctx,target["organization_id"])
    return set_target_enabled(target_id,enabled)

@router.post("/deployment-targets/{target_id}/deploy")
async def deployment_start(target_id: str, item: DeploymentRequest, ctx: ActorContext=Depends(require_permission("deployments:write"))):
    target=get_target(target_id)
    if not target: raise HTTPException(404,"deployment target not found")
    ensure_org_scope(ctx,target["organization_id"])
    try: return start_deployment(target_id,item,ctx.actor_id)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/deployments")
async def deployments(project_id: str|None=None, limit: int=100, ctx: ActorContext=Depends(require_permission("deployments:read"))):
    return list_deployments(ctx.organization_id,project_id,limit)

@router.get("/deployments/{run_id}")
async def deployment_get(run_id: str, ctx: ActorContext=Depends(require_permission("deployments:read"))):
    run=get_deployment(run_id)
    if not run: raise HTTPException(404,"deployment not found")
    ensure_org_scope(ctx,run["organization_id"]); return run

@router.get("/deployment-targets/{target_id}/releases")
async def deployment_releases(target_id: str, ctx: ActorContext=Depends(require_permission("deployments:read"))):
    target=get_target(target_id)
    if not target: raise HTTPException(404,"deployment target not found")
    ensure_org_scope(ctx,target["organization_id"]); return list_releases(target_id)

@router.get("/artifacts")
async def artifacts(project_id: str|None=None, limit: int=100, ctx: ActorContext=Depends(require_permission("projects:read"))):
    return list_artifacts(ctx.organization_id,project_id,limit)

@router.post("/artifacts/prune")
async def artifacts_prune(retention_days: int|None=None, max_per_project: int|None=None, dry_run: bool=True, ctx: ActorContext=Depends(require_permission("projects:write"))):
    return prune_artifacts(organization_id=ctx.organization_id, retention_days=retention_days, max_per_project=max_per_project, dry_run=dry_run)


@router.get("/operator/catalog")
async def operator_catalog(organization_id: str|None=None, ctx: ActorContext=Depends(require_permission("applications:read"))):
    org=ctx.organization_id or organization_id or "global"
    ensure_org_scope(ctx,org); return tool_catalog(org)

@router.post("/operator/command")
async def operator_command(item: OperatorCommandRequest, ctx: ActorContext=Depends(require_permission("actions:execute"))):
    org=ctx.organization_id or item.organization_id or "global"
    ensure_org_scope(ctx,org)
    try: return await execute_operator(item.command,org,ctx.actor_id,item.dry_run)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc
    except RuntimeError as exc: raise HTTPException(503,str(exc)) from exc

@router.get("/operator/runs")
async def operator_runs(limit: int=100, ctx: ActorContext=Depends(require_permission("deployments:read"))):
    return list_operator_runs(ctx.organization_id,limit)

@router.get("/operator/runs/{run_id}")
async def operator_run(run_id: str, ctx: ActorContext=Depends(require_permission("deployments:read"))):
    run=get_operator_run(run_id)
    if not run: raise HTTPException(404,"operator run not found")
    ensure_org_scope(ctx,run["organization_id"]); return run


@router.post("/developer/sessions")
async def developer_session_start(item: DeveloperSessionRequest, ctx: ActorContext=Depends(require_permission("projects:write"))):
    project=get_project(item.project_id)
    if not project: raise HTTPException(404,"project not found")
    ensure_org_scope(ctx,project["organization_id"])
    try: return start_developer_session(item,ctx.actor_id,project["organization_id"])
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/developer/sessions")
async def developer_sessions(project_id: str|None=None, limit: int=100, ctx: ActorContext=Depends(require_permission("projects:read"))):
    if project_id:
        project=get_project(project_id)
        if not project: raise HTTPException(404,"project not found")
        ensure_org_scope(ctx,project["organization_id"])
    return list_developer_sessions(ctx.organization_id,project_id,limit)

@router.get("/developer/sessions/{session_id}")
async def developer_session_get(session_id: str, ctx: ActorContext=Depends(require_permission("projects:read"))):
    session=get_developer_session(session_id)
    if not session: raise HTTPException(404,"developer session not found")
    ensure_org_scope(ctx,session["organization_id"]); return session
