from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.connectors.base import BaseConnector, ConnectorError


class SimulatorConnector(BaseConnector):
    async def execute(self, capability: str, parameters: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        known = {c.name for c in self.manifest.capabilities}
        if capability not in known:
            raise ConnectorError(f"capability not declared: {capability}")
        if parameters.get("simulate_failure"):
            raise ConnectorError("simulated connector failure")
        return {
            "ok": not bool(parameters.get("simulate_verification_failure")),
            "simulated": True,
            "dry_run": dry_run,
            "application": self.manifest.id,
            "capability": capability,
            "parameters": parameters,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    async def verify(self, capability: str, parameters: dict[str, Any], result: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        if parameters.get("simulate_verification_failure"):
            return {"ok": False, "reason": "simulated verification failure"}
        return {"ok": True, "simulated": True, "capability": capability}

    async def rollback(self, capability: str, parameters: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "simulated": True, "rolled_back": capability}
