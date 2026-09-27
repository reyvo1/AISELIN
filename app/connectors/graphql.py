from __future__ import annotations

import os
from typing import Any

import httpx

from app.connectors.base import BaseConnector, ConnectorError
from app.connectors.security import require_allowed_target
from app.core.config import settings
from app.services.vault import get_secret


class GraphQLConnector(BaseConnector):
    def _config(self) -> tuple[str, dict[str, str], dict[str, Any]]:
        url=self.manifest.connector.base_url
        if not url: raise ConnectorError("GraphQL connector requires base_url")
        require_allowed_target(url)
        headers=dict(self.manifest.connector.headers)
        secret_ref=self.manifest.connector.secret_ref
        if secret_ref:
            secret=get_secret(f"connector:{self.manifest.id}",secret_ref) or os.getenv(secret_ref)
            if not secret: raise ConnectorError(f"connector secret not found: {secret_ref}")
            headers.setdefault("Authorization",f"Bearer {secret}")
        operations=self.manifest.metadata.get("graphql",{}).get("operations",{})
        return url,headers,operations

    async def _post(self, query: str, variables: dict[str,Any]) -> dict[str,Any]:
        url,headers,_=self._config()
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                r=await client.post(url,json={"query":query,"variables":variables},headers=headers)
                r.raise_for_status(); data=r.json()
        except (httpx.HTTPError,ValueError) as exc:
            raise ConnectorError(f"GraphQL connector failed: {exc}") from exc
        if not isinstance(data,dict): raise ConnectorError("GraphQL connector returned non-object JSON")
        if data.get("errors"): raise ConnectorError(f"GraphQL errors: {data['errors']}")
        return {"ok":True,"data":data.get("data",{})}

    async def execute(self, capability: str, parameters: dict[str,Any], dry_run: bool=False) -> dict[str,Any]:
        _,_,ops=self._config(); spec=ops.get(capability)
        if not spec: raise ConnectorError(f"GraphQL operation is not configured for capability: {capability}")
        if isinstance(spec,str): query=spec
        else: query=spec.get("query")
        if not query: raise ConnectorError("GraphQL operation query is empty")
        return await self._post(query,{**parameters,"dry_run":dry_run})

    async def health(self) -> dict[str,Any]:
        try:
            _,_,ops=self._config(); query=self.manifest.metadata.get("graphql",{}).get("health_query")
            if not query: return {"ok":True,"connector":"graphql","configured_operations":len(ops)}
            return await self._post(query,{})
        except Exception as exc: return {"ok":False,"error":str(exc)}
