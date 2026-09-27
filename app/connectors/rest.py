from __future__ import annotations

import os
from typing import Any

import httpx

from app.connectors.base import BaseConnector, ConnectorError
from app.core.config import settings
from app.services.vault import get_secret
from app.connectors.security import require_allowed_target



class RestConnector(BaseConnector):
    def _config(self) -> tuple[str, dict[str, str]]:
        base_url = self.manifest.connector.base_url
        if not base_url:
            raise ConnectorError("REST connector requires base_url")
        require_allowed_target(base_url)
        headers = dict(self.manifest.connector.headers)
        secret_ref = self.manifest.connector.secret_ref
        if secret_ref:
            secret = get_secret(f"connector:{self.manifest.id}", secret_ref) or os.getenv(secret_ref)
            if not secret:
                raise ConnectorError(f"connector secret not found: {secret_ref}")
            headers.setdefault("Authorization", f"Bearer {secret}")
        return base_url.rstrip("/"), headers

    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]:
        base_url, headers = self._config()
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                response = await client.request(method, base_url + path, json=json, headers=headers)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ConnectorError(f"REST connector failed: {exc}") from exc
        if not isinstance(data, dict):
            raise ConnectorError("REST connector returned non-object JSON")
        return data

    async def execute(self, capability: str, parameters: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        return await self._request("POST", "/actions/execute", json={"capability": capability, "parameters": parameters, "dry_run": dry_run})

    async def health(self) -> dict[str, Any]:
        try:
            data = await self._request("GET", "/health")
            return {"ok": True, "remote": data}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def discover(self) -> dict[str, Any]:
        try:
            return await self._request("GET", "/capabilities")
        except Exception:
            return await super().discover()

    async def verify(self, capability: str, parameters: dict[str, Any], result: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/actions/verify", json={"capability": capability, "parameters": parameters, "result": result, "verification": spec})

    async def rollback(self, capability: str, parameters: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/actions/rollback", json={"capability": capability, "parameters": parameters, "result": result})
