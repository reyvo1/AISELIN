from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from app.connectors.factory import build_connector
from app.models import ActionRequest, ActionResult, PolicyLevel
from app.services.approvals import create_approval
from app.services.audit import audit
from app.services.circuit import allow as circuit_allow, record_failure, record_success
from app.services.idempotency import find as find_idempotent, save as save_idempotent
from app.services.incidents import open_incident
from app.services.policy import policy_service
from app.services.registry import registry
from app.services.verifier import rollback, save_verification, verify


def _constraints_ok(parameters: dict[str, Any], constraints: dict[str, Any]) -> tuple[bool, str]:
    required = constraints.get("required_parameters", [])
    missing = [key for key in required if key not in parameters]
    if missing:
        return False, f"missing required parameters: {', '.join(missing)}"
    limits = constraints.get("max_numeric", {})
    for key, maximum in limits.items():
        if key in parameters:
            try:
                if float(parameters[key]) > float(maximum): return False, f"parameter {key} exceeds maximum {maximum}"
            except (TypeError, ValueError): return False, f"parameter {key} must be numeric"
    allowed_values = constraints.get("allowed_values", {})
    for key, values in allowed_values.items():
        if key in parameters and parameters[key] not in values: return False, f"parameter {key} is not in allowed values"
    return True, "ok"


async def execute_action(req: ActionRequest, *, approval_granted: bool = False) -> ActionResult:
    run_id = str(uuid4())
    cached = find_idempotent(req.idempotency_key)
    if cached:
        data = cached["result"]
        return ActionResult(status=data["status"], run_id=cached["id"], app_id=req.app_id, capability=req.capability,
                            message="idempotent replay: previous result returned", data=data.get("data", {}), approval_id=data.get("approval_id"))
    manifest = registry.get(req.app_id)
    if not manifest:
        return ActionResult(status="blocked", run_id=run_id, app_id=req.app_id, capability=req.capability, message="application not registered")
    capability = registry.capability(req.app_id, req.capability)
    if not capability:
        audit(actor=req.actor, app_id=req.app_id, capability=req.capability, action="execute", decision="blocked", reason="capability not declared", request=req.model_dump(mode="json"))
        return ActionResult(status="blocked", run_id=run_id, app_id=req.app_id, capability=req.capability, message="capability not declared by application")

    level, constraints = policy_service.resolve(manifest, req.capability)
    allowed, reason = _constraints_ok(req.parameters, constraints)
    if not allowed:
        audit(actor=req.actor, app_id=req.app_id, capability=req.capability, action="execute", decision="blocked", reason=reason, request=req.model_dump(mode="json"))
        return ActionResult(status="blocked", run_id=run_id, app_id=req.app_id, capability=req.capability, message=reason)
    if level == PolicyLevel.FORBIDDEN:
        audit(actor=req.actor, app_id=req.app_id, capability=req.capability, action="execute", decision="blocked", reason="policy forbids action", request=req.model_dump(mode="json"))
        return ActionResult(status="blocked", run_id=run_id, app_id=req.app_id, capability=req.capability, message="action forbidden by policy")
    if level == PolicyLevel.APPROVAL and not approval_granted:
        approval_id = create_approval(req.app_id, req.capability, req.actor, req.model_dump(mode="json"))
        audit(actor=req.actor, app_id=req.app_id, capability=req.capability, action="execute", decision="approval_required", reason="policy requires owner approval", request=req.model_dump(mode="json"), result={"approval_id": approval_id})
        return ActionResult(status="approval_required", run_id=run_id, app_id=req.app_id, capability=req.capability, message="owner approval required", approval_id=approval_id)

    circuit_ok, circuit_state = circuit_allow(req.app_id, req.capability)
    if not circuit_ok:
        audit(actor=req.actor, app_id=req.app_id, capability=req.capability, action="execute", decision="blocked", reason="circuit breaker open", request=req.model_dump(mode="json"))
        return ActionResult(status="blocked", run_id=run_id, app_id=req.app_id, capability=req.capability, message="circuit breaker is open")

    connector = build_connector(manifest)
    retry_spec = capability.retry or {}
    max_attempts = max(1, min(int(retry_spec.get("max_attempts", 1)), 10))
    backoff = float(retry_spec.get("backoff_seconds", 0.1))
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            data = await connector.execute(req.capability, req.parameters, dry_run=req.dry_run)
            verified, verification = await verify(connector, capability, req.parameters, data)
            if not verified and not req.dry_run:
                rollback_data = await rollback(connector, capability, req.parameters, data)
                save_verification(run_id, req.app_id, req.capability, "failed", verification, rollback_data)
                state=record_failure(req.app_id, req.capability, constraints.get("circuit_threshold"), constraints.get("circuit_reset_seconds"))
                incident=open_incident(organization_id=manifest.organization_id, app_id=req.app_id, severity="high",
                    title=f"Verification failed: {req.capability}", description=str(verification),
                    fingerprint=f"verify:{req.app_id}:{req.capability}", metadata={"run_id":run_id,"rollback":rollback_data,"circuit":state})
                result=ActionResult(status="failed",run_id=run_id,app_id=req.app_id,capability=req.capability,message="post-action verification failed",data={"action":data,"verification":verification,"rollback":rollback_data,"incident_id":incident["id"]})
                audit(actor=req.actor, app_id=req.app_id, capability=req.capability, action="execute", decision="failed", reason="verification failed", request=req.model_dump(mode="json"), result=result.data)
                save_idempotent(run_id, req.idempotency_key, req.app_id, req.capability, "failed", req.model_dump(mode="json"), result.model_dump(mode="json"))
                return result
            save_verification(run_id, req.app_id, req.capability, "success", verification, {})
            record_success(req.app_id, req.capability)
            status = "dry_run" if req.dry_run else "success"
            enriched={**data,"verification":verification,"attempt":attempt,"circuit_before":circuit_state}
            result = ActionResult(status=status, run_id=run_id, app_id=req.app_id, capability=req.capability, message="action completed" if not req.dry_run else "dry-run completed", data=enriched)
            audit(actor=req.actor, app_id=req.app_id, capability=req.capability, action="execute", decision=status, reason=req.reason, request=req.model_dump(mode="json"), result=enriched)
            save_idempotent(run_id, req.idempotency_key, req.app_id, req.capability, status, req.model_dump(mode="json"), result.model_dump(mode="json"))
            return result
        except Exception as exc:
            last_error=exc
            if attempt < max_attempts:
                await asyncio.sleep(min(backoff * attempt, 2.0))

    state=record_failure(req.app_id, req.capability, constraints.get("circuit_threshold"), constraints.get("circuit_reset_seconds"))
    data={"error":str(last_error),"attempts":max_attempts,"circuit":state}
    incident=open_incident(organization_id=manifest.organization_id,app_id=req.app_id,severity="high",title=f"Action failed: {req.capability}",description=str(last_error),fingerprint=f"action:{req.app_id}:{req.capability}",metadata={"run_id":run_id,"attempts":max_attempts,"circuit":state})
    data["incident_id"]=incident["id"]
    audit(actor=req.actor, app_id=req.app_id, capability=req.capability, action="execute", decision="failed", reason=req.reason, request=req.model_dump(mode="json"), result=data)
    result=ActionResult(status="failed", run_id=run_id, app_id=req.app_id, capability=req.capability, message=str(last_error), data=data)
    save_idempotent(run_id, req.idempotency_key, req.app_id, req.capability, "failed", req.model_dump(mode="json"), result.model_dump(mode="json"))
    return result
