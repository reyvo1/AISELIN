from __future__ import annotations

import json

import httpx

from app.core.config import settings
from app.models import PlannedAction


async def plan_with_openai_compatible(command: str, registry_snapshot: list[dict]) -> list[PlannedAction]:
    if not (settings.ai_base_url and settings.ai_api_key and settings.ai_model):
        raise RuntimeError("AI provider is not configured")
    system = (
        "You are an operations planner. Return ONLY JSON array. Each item must contain app_id, capability, "
        "parameters object, rationale. You may only use app_id/capability pairs listed in REGISTRY. "
        "Never invent capabilities. Destructive operations still go through downstream policy.\nREGISTRY:\n" +
        json.dumps(registry_snapshot, ensure_ascii=False)
    )
    payload = {
        "model": settings.ai_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": command}],
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {settings.ai_api_key}"}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(settings.ai_base_url.rstrip("/") + "/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    raw = json.loads(content)
    if not isinstance(raw, list):
        raise RuntimeError("planner returned non-array JSON")
    return [PlannedAction.model_validate(x) for x in raw]
