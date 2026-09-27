from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import Header, HTTPException, Request

from app.core.config import settings
from app.core.db import connect

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {"*"},
    "admin": {"applications:read","applications:write","actions:execute","approvals:read","approvals:resolve","policies:read","policies:write","rules:read","rules:write","audit:read","incidents:read","incidents:resolve","resources:read","resources:write","jobs:read","jobs:write","org:read","org:write","notifications:read","notifications:write","ai:read","agents:read","agents:write","projects:read","projects:write","deployments:read","deployments:write"},
    "operator": {"applications:read","actions:execute","approvals:read","rules:read","audit:read","incidents:read","incidents:resolve","resources:read","jobs:read","notifications:read","ai:read","agents:read","projects:read","deployments:read","deployments:write"},
    "viewer": {"applications:read","policies:read","rules:read","audit:read","incidents:read","resources:read","jobs:read","notifications:read","ai:read","agents:read","projects:read","deployments:read"},
}

@dataclass(frozen=True)
class ActorContext:
    actor_id: str
    organization_id: str | None
    role: str
    auth_type: str
    def can(self, permission: str) -> bool:
        permissions=ROLE_PERMISSIONS.get(self.role,set()); return "*" in permissions or permission in permissions


def _hash_api_key(token: str) -> str: return hashlib.sha256(token.encode()).hexdigest()


async def _authenticate(x_owner_token: str | None, authorization: str | None) -> ActorContext:
    if x_owner_token and hmac.compare_digest(x_owner_token,settings.owner_token):
        return ActorContext("owner",None,"owner","owner-token")
    if authorization and authorization.lower().startswith("bearer "):
        token=authorization.split(" ",1)[1].strip()
        if token:
            digest=_hash_api_key(token)
            with connect() as conn:
                row=conn.execute("""SELECT k.*,p.enabled AS principal_enabled FROM api_keys k JOIN principals p ON p.id=k.principal_id WHERE k.key_hash=?""",(digest,)).fetchone()
            if row and not row["revoked_at"] and int(row["principal_enabled"])==1:
                if row["expires_at"]:
                    try:
                        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc): raise HTTPException(401,"API key expired")
                    except ValueError: raise HTTPException(401,"API key expiry is invalid")
                return ActorContext(row["principal_id"],row["organization_id"],row["role"],"api-key")
            if settings.oidc_enabled:
                from app.auth.oidc import OidcError, validate_oidc_token
                try: claims=await validate_oidc_token(token)
                except OidcError as exc: raise HTTPException(401,str(exc)) from exc
                role=str(claims.get(settings.oidc_role_claim,"viewer"))
                if role not in ROLE_PERMISSIONS: raise HTTPException(403,"OIDC role is not recognized")
                org=claims.get(settings.oidc_org_claim)
                if not org: raise HTTPException(403,"OIDC organization claim is required")
                actor=str(claims.get(settings.oidc_principal_claim) or claims.get("sub") or "oidc-user")
                # OIDC identities are always tenant-scoped; platform-owner authority remains local owner-token only.
                return ActorContext(actor,str(org),role,"oidc")
    raise HTTPException(401,"authorization required")


def require_permission(permission: str) -> Callable:
    async def dependency(request: Request,x_owner_token: str|None=Header(default=None),authorization: str|None=Header(default=None)) -> ActorContext:
        ctx=await _authenticate(x_owner_token,authorization)
        if not ctx.can(permission): raise HTTPException(403,f"permission denied: {permission}")
        request.state.actor_context=ctx; return ctx
    return dependency


async def require_owner(request: Request,x_owner_token: str|None=Header(default=None),authorization: str|None=Header(default=None)) -> str:
    ctx=await _authenticate(x_owner_token,authorization)
    if ctx.role!="owner" or ctx.organization_id is not None:
        raise HTTPException(403,"platform owner authorization required")
    request.state.actor_context=ctx; return ctx.actor_id


def require_event_source(x_event_token: str|None=Header(default=None)) -> str:
    if not x_event_token or not hmac.compare_digest(x_event_token,settings.event_token): raise HTTPException(401,"event source authorization required")
    return "event-source"


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    organization_id: str
    kind: str
    capabilities: tuple[str, ...]


async def require_agent(authorization: str|None=Header(default=None)) -> AgentContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401,"agent authorization required")
    token=authorization.split(" ",1)[1].strip()
    if not token:
        raise HTTPException(401,"agent authorization required")
    digest=_hash_api_key(token)
    with connect() as conn:
        row=conn.execute("SELECT id,organization_id,kind,capabilities_json,status FROM agent_nodes WHERE token_hash=?",(digest,)).fetchone()
    if not row:
        raise HTTPException(401,"invalid agent token")
    import json
    capabilities=tuple(json.loads(row["capabilities_json"]))
    return AgentContext(str(row["id"]),str(row["organization_id"]),str(row["kind"]),capabilities)


def ensure_org_scope(ctx: ActorContext,organization_id: str) -> None:
    if ctx.organization_id is not None and ctx.organization_id != organization_id: raise HTTPException(403,"organization scope denied")
