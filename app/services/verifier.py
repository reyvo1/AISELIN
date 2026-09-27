from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.connectors.base import BaseConnector
from app.core.db import connect, dumps
from app.models import Capability


def _path(data: dict[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


async def verify(connector: BaseConnector, capability: Capability, parameters: dict[str, Any], result: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    spec = capability.verification or {}
    if not spec:
        return True, {"mode": "none", "verified": True}
    mode = spec.get("mode", "result")
    if mode == "connector":
        data = await connector.verify(capability.name, parameters, result, spec)
        return bool(data.get("ok")), data
    checks = spec.get("checks", [])
    if not checks and "path" in spec:
        checks = [spec]
    failures=[]
    for check in checks:
        actual=_path(result, check.get("path", "ok"))
        if "equals" in check and actual != check["equals"]:
            failures.append({"path":check.get("path","ok"),"actual":actual,"expected":check["equals"]})
        if check.get("truthy") and not actual:
            failures.append({"path":check.get("path","ok"),"actual":actual,"expected":"truthy"})
    return not failures, {"mode":"result","verified":not failures,"failures":failures}


async def rollback(connector: BaseConnector, capability: Capability, parameters: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if capability.rollback_capability:
        rollback_params={"original_parameters":parameters,"original_result":result}
        return await connector.execute(capability.rollback_capability, rollback_params, dry_run=False)
    return await connector.rollback(capability.name, parameters, result)


def save_verification(run_id: str, app_id: str, capability: str, status: str, verification: dict[str, Any], rollback_data: dict[str, Any] | None=None) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO action_verifications VALUES(?,?,?,?,?,?,?,?)",
                     (str(uuid4()),run_id,app_id,capability,status,dumps(verification),dumps(rollback_data or {}),datetime.now(timezone.utc).isoformat()))
