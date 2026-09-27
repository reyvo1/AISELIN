from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models import ApplicationManifest


class ConnectorError(RuntimeError):
    pass


class BaseConnector(ABC):
    def __init__(self, manifest: ApplicationManifest):
        self.manifest = manifest

    @abstractmethod
    async def execute(self, capability: str, parameters: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        raise NotImplementedError

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "connector": self.manifest.connector.type}

    async def discover(self) -> dict[str, Any]:
        return {"ok": True, "capabilities": [c.model_dump(mode="json") for c in self.manifest.capabilities]}

    async def verify(self, capability: str, parameters: dict[str, Any], result: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        return {"ok": bool(result.get("ok", True)), "mode": "connector-default"}

    async def rollback(self, capability: str, parameters: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "supported": False, "message": "connector rollback is not implemented"}
