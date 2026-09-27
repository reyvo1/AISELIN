from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from app.core.config import settings
from app.core.db import connect
from app.models import PlannedAction


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    priority: int = 100
    daily_request_limit: int = 0
    daily_token_limit: int = 0
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_provider_configs() -> list[ProviderConfig]:
    rows: list[dict[str, Any]] = []
    if settings.ai_providers_json:
        try:
            parsed = json.loads(settings.ai_providers_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AIOC_AI_PROVIDERS_JSON is invalid JSON") from exc
        if not isinstance(parsed, list):
            raise RuntimeError("AIOC_AI_PROVIDERS_JSON must be a JSON array")
        rows = parsed
    elif settings.ai_base_url and settings.ai_model:
        rows = [{
            "name": "legacy-primary", "base_url": settings.ai_base_url, "api_key": settings.ai_api_key,
            "model": settings.ai_model, "priority": 100,
        }]
    providers=[]
    for row in rows:
        api_key = row.get("api_key", "")
        env_name = row.get("api_key_env")
        if env_name: api_key = os.getenv(env_name, "")
        providers.append(ProviderConfig(
            name=str(row.get("name") or row.get("model") or "provider"),
            base_url=str(row.get("base_url", "")).rstrip("/"),
            api_key=api_key,
            model=str(row.get("model", "")),
            priority=int(row.get("priority", 100)),
            daily_request_limit=int(row.get("daily_request_limit", settings.ai_daily_request_limit)),
            daily_token_limit=int(row.get("daily_token_limit", settings.ai_daily_token_limit)),
            input_cost_per_million=float(row.get("input_cost_per_million", 0)),
            output_cost_per_million=float(row.get("output_cost_per_million", 0)),
        ))
    return sorted(providers, key=lambda x: x.priority)


def usage_for(provider: ProviderConfig) -> dict[str, Any]:
    with connect() as conn:
        row=conn.execute("SELECT * FROM ai_usage WHERE provider_name=? AND model=? AND day=?",
                         (provider.name,provider.model,_today())).fetchone()
    return dict(row) if row else {"requests":0,"input_tokens":0,"output_tokens":0,"estimated_cost":0.0}


def _within_budget(provider: ProviderConfig) -> bool:
    usage=usage_for(provider)
    if provider.daily_request_limit and int(usage["requests"]) >= provider.daily_request_limit: return False
    if provider.daily_token_limit and int(usage["input_tokens"])+int(usage["output_tokens"]) >= provider.daily_token_limit: return False
    return True


def record_usage(provider: ProviderConfig, input_tokens: int, output_tokens: int) -> None:
    day=_today(); now=_now()
    cost=(input_tokens/1_000_000)*provider.input_cost_per_million + (output_tokens/1_000_000)*provider.output_cost_per_million
    with connect() as conn:
        row=conn.execute("SELECT id,requests,input_tokens,output_tokens,estimated_cost FROM ai_usage WHERE provider_name=? AND model=? AND day=?",
                         (provider.name,provider.model,day)).fetchone()
        if row:
            conn.execute("UPDATE ai_usage SET requests=?,input_tokens=?,output_tokens=?,estimated_cost=?,updated_at=? WHERE id=?",
                         (int(row["requests"])+1,int(row["input_tokens"])+input_tokens,int(row["output_tokens"])+output_tokens,float(row["estimated_cost"])+cost,now,row["id"]))
        else:
            conn.execute("INSERT INTO ai_usage VALUES(?,?,?,?,?,?,?,?,?)",
                         (str(uuid4()),provider.name,provider.model,day,1,input_tokens,output_tokens,cost,now))


async def _call(provider: ProviderConfig, command: str, registry_snapshot: list[dict]) -> list[PlannedAction]:
    if not provider.base_url or not provider.model or not provider.api_key:
        raise RuntimeError(f"provider not fully configured: {provider.name}")
    system=("You are an operations planner. Return ONLY JSON array. Each item must contain app_id, capability, parameters object, rationale. "
            "Use only app_id/capability pairs in REGISTRY. Never invent tools. Never bypass downstream policy.\nREGISTRY:\n"+
            json.dumps(registry_snapshot,ensure_ascii=False))
    payload={"model":provider.model,"messages":[{"role":"system","content":system},{"role":"user","content":command}],"temperature":0}
    headers={"Authorization":f"Bearer {provider.api_key}"}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response=await client.post(provider.base_url+"/chat/completions",json=payload,headers=headers)
        response.raise_for_status(); body=response.json()
    content=body["choices"][0]["message"]["content"]; raw=json.loads(content)
    if not isinstance(raw,list): raise RuntimeError("planner returned non-array JSON")
    if len(raw) > settings.ai_max_plan_actions: raise RuntimeError("planner exceeded AIOC_AI_MAX_PLAN_ACTIONS")
    usage=body.get("usage") or {}; record_usage(provider,int(usage.get("prompt_tokens",0)),int(usage.get("completion_tokens",0)))
    return [PlannedAction.model_validate(x) for x in raw]


async def plan_with_router(command: str, registry_snapshot: list[dict]) -> list[PlannedAction]:
    providers=load_provider_configs()
    if not providers: raise RuntimeError("no AI providers configured")
    errors=[]
    for provider in providers:
        if not _within_budget(provider):
            errors.append(f"{provider.name}: budget exhausted"); continue
        try: return await _call(provider,command,registry_snapshot)
        except Exception as exc: errors.append(f"{provider.name}: {exc}")
    raise RuntimeError("all AI providers unavailable: " + " | ".join(errors))


def provider_status() -> list[dict[str, Any]]:
    out=[]
    for provider in load_provider_configs():
        usage=usage_for(provider)
        out.append({"name":provider.name,"model":provider.model,"priority":provider.priority,"configured":bool(provider.base_url and provider.api_key and provider.model),
                    "within_budget":_within_budget(provider),"usage":usage,"daily_request_limit":provider.daily_request_limit,"daily_token_limit":provider.daily_token_limit})
    return out


async def _call_operator(provider: ProviderConfig, command: str, catalog: dict[str, Any]):
    from app.models import OperatorPlannedAction
    if not provider.base_url or not provider.model or not provider.api_key:
        raise RuntimeError(f"provider not fully configured: {provider.name}")
    system=(
        "You are the planner for an autonomous operations platform. Return ONLY a JSON array. "
        "Each item must contain tool, parameters object, rationale. Use only tools/resources in CATALOG. "
        "Never invent agent IDs, project IDs, deployment targets, applications or capabilities. "
        "Never bypass downstream policy or approval. Prefer project.deploy for requests to sync/deploy/online a registered project; "
        "project.develop for requests to inspect/fix/change/develop source code; server.diagnose for autonomous bounded troubleshooting of a registered server; agent.task for one-shot server/local-agent operations; project.inspect for repository inspection; application.action for registered application capabilities.\nCATALOG:\n"
        + json.dumps(catalog,ensure_ascii=False)
    )
    payload={"model":provider.model,"messages":[{"role":"system","content":system},{"role":"user","content":command}],"temperature":0}
    headers={"Authorization":f"Bearer {provider.api_key}"}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response=await client.post(provider.base_url+"/chat/completions",json=payload,headers=headers)
        response.raise_for_status(); body=response.json()
    raw=json.loads(body["choices"][0]["message"]["content"])
    if not isinstance(raw,list): raise RuntimeError("operator planner returned non-array JSON")
    if len(raw) > settings.ai_max_plan_actions: raise RuntimeError("operator planner exceeded AIOC_AI_MAX_PLAN_ACTIONS")
    usage=body.get("usage") or {}; record_usage(provider,int(usage.get("prompt_tokens",0)),int(usage.get("completion_tokens",0)))
    return [OperatorPlannedAction.model_validate(x) for x in raw]


async def plan_operator_with_router(command: str, catalog: dict[str, Any]):
    providers=load_provider_configs(); errors=[]
    if not providers: raise RuntimeError("no AI providers configured")
    for provider in providers:
        if not _within_budget(provider): errors.append(f"{provider.name}: budget exhausted"); continue
        try: return await _call_operator(provider,command,catalog)
        except Exception as exc: errors.append(f"{provider.name}: {exc}")
    raise RuntimeError("all AI providers unavailable: " + " | ".join(errors))


async def _call_developer(provider: ProviderConfig, objective: str, project: dict[str, Any], context: dict[str, Any]):
    from app.models import DeveloperPlannedAction
    if not provider.base_url or not provider.model or not provider.api_key:
        raise RuntimeError(f"provider not fully configured: {provider.name}")
    system=(
        "You are an autonomous senior software engineer operating through a restricted repository agent. "
        "Return ONLY one JSON object with action, parameters, rationale. Allowed actions: read, search, patch, test, finish, fail. "
        "Use read for a relative file path, search for a literal query, patch for a unified diff, test to run the project's preconfigured test steps, "
        "finish only when the objective is satisfied and verification is adequate, fail only when blocked by missing information or unsafe constraints. "
        "Never request shell commands, never access paths outside the repository, never modify .git, secrets, credentials, CI protections, or production safety gates merely to make tests pass. "
        "Prefer root-cause fixes and small coherent patches. Existing repository changes are not assumed safe.\n"
        "PROJECT:\n"+json.dumps(project,ensure_ascii=False)+"\nCONTEXT:\n"+json.dumps(context,ensure_ascii=False)
    )
    payload={"model":provider.model,"messages":[{"role":"system","content":system},{"role":"user","content":objective}],"temperature":0}
    headers={"Authorization":f"Bearer {provider.api_key}"}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response=await client.post(provider.base_url+"/chat/completions",json=payload,headers=headers)
        response.raise_for_status(); body=response.json()
    raw=json.loads(body["choices"][0]["message"]["content"])
    usage=body.get("usage") or {}; record_usage(provider,int(usage.get("prompt_tokens",0)),int(usage.get("completion_tokens",0)))
    return DeveloperPlannedAction.model_validate(raw)


async def plan_developer_with_router(objective: str, project: dict[str, Any], context: dict[str, Any]):
    providers=load_provider_configs(); errors=[]
    if not providers: raise RuntimeError("no AI providers configured")
    for provider in providers:
        if not _within_budget(provider): errors.append(f"{provider.name}: budget exhausted"); continue
        try: return await _call_developer(provider,objective,project,context)
        except Exception as exc: errors.append(f"{provider.name}: {exc}")
    raise RuntimeError("all AI providers unavailable: " + " | ".join(errors))


async def _call_server(provider: ProviderConfig, objective: str, agent: dict[str, Any], context: dict[str, Any]):
    from app.models import ServerPlannedAction
    if not provider.base_url or not provider.model or not provider.api_key:
        raise RuntimeError(f"provider not fully configured: {provider.name}")
    system = (
        "You are an autonomous Ubuntu/server operations troubleshooter using a restricted edge agent. "
        "Return ONLY one JSON object with action, parameters, rationale. Allowed actions: "
        "system_status, process_list, network_sockets, network_routes, firewall_status, package_updates, package_security_updates, package_apply_updates, service_status, service_logs, service_restart, docker_list, docker_logs, docker_restart, finish, fail. "
        "Use ONLY capabilities declared by AGENT. package_apply_updates may request ONLY explicit package names and is always subject to infrastructure approval policy. Never request arbitrary shell, firewall mutation, user/password changes, disk formatting, database deletion, or destructive filesystem commands. "
        "Prefer observe -> diagnose -> smallest safe recovery -> verify. A restart is allowed only when evidence suggests it is relevant. "
        "finish only when the objective is resolved or a useful bounded diagnosis is complete; fail if required capability is unavailable or action would be unsafe.\n"
        "AGENT:\n" + json.dumps(agent, ensure_ascii=False) + "\nCONTEXT:\n" + json.dumps(context, ensure_ascii=False)
    )
    payload={"model":provider.model,"messages":[{"role":"system","content":system},{"role":"user","content":objective}],"temperature":0}
    headers={"Authorization":f"Bearer {provider.api_key}"}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response=await client.post(provider.base_url+"/chat/completions",json=payload,headers=headers)
        response.raise_for_status(); body=response.json()
    raw=json.loads(body["choices"][0]["message"]["content"])
    usage=body.get("usage") or {}; record_usage(provider,int(usage.get("prompt_tokens",0)),int(usage.get("completion_tokens",0)))
    return ServerPlannedAction.model_validate(raw)


async def plan_server_with_router(objective: str, agent: dict[str, Any], context: dict[str, Any]):
    providers=load_provider_configs(); errors=[]
    if not providers: raise RuntimeError("no AI providers configured")
    for provider in providers:
        if not _within_budget(provider): errors.append(f"{provider.name}: budget exhausted"); continue
        try: return await _call_server(provider,objective,agent,context)
        except Exception as exc: errors.append(f"{provider.name}: {exc}")
    raise RuntimeError("all AI providers unavailable: " + " | ".join(errors))
