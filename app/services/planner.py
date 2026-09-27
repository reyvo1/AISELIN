from __future__ import annotations

import json
import re

from app.ai.router import plan_with_router
from app.core.config import settings
from app.models import PlannedAction
from app.services.registry import registry


def registry_snapshot() -> list[dict]:
    return [{"id":app.id,"name":app.name,"type":app.type,"organization_id":app.organization_id,"capabilities":[c.name for c in app.capabilities]} for app in registry.list()]


def _fallback_plan(command: str) -> list[PlannedAction]:
    match=re.match(r"^\s*(?:run|jalankan)\s+([\w.-]+)\s+([\w.-]+)(?:\s+(\{.*\}))?\s*$",command,re.I|re.S)
    if not match:
        raise ValueError("rules planner needs explicit syntax: 'run <app_id> <capability> {json}'. Configure AIOC_AI_PROVIDER=router for free-form natural language planning.")
    app_id,capability,raw=match.groups(); parameters=json.loads(raw) if raw else {}
    if not registry.capability_exists(app_id,capability): raise ValueError("requested app/capability is not registered")
    return [PlannedAction(app_id=app_id,capability=capability,parameters=parameters,rationale="deterministic command parser")]


async def plan(command: str) -> list[PlannedAction]:
    if settings.ai_provider in {"router","openai-compatible"}:
        actions=await plan_with_router(command,registry_snapshot())
        for action in actions:
            if not registry.capability_exists(action.app_id,action.capability):
                raise ValueError(f"AI attempted undeclared capability: {action.app_id}/{action.capability}")
        return actions
    return _fallback_plan(command)
