from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

from app.connectors.base import BaseConnector, ConnectorError
from app.core.config import settings
from app.services.vault import get_secret
from app.connectors.security import require_allowed_target

_ALLOWED_METHODS={"GET","POST","PUT","PATCH","DELETE","HEAD"}



def _render(template: str, parameters: dict[str, Any]) -> str:
    out=template
    for key,value in parameters.items():
        out=out.replace("{"+key+"}", quote(str(value), safe=""))
    if "{" in out or "}" in out:
        raise ConnectorError("mapped connector path has unresolved parameters")
    return out


class MappedHttpConnector(BaseConnector):
    """Declarative HTTP transport. Every operation must be explicitly declared in manifest.metadata.operations."""
    default_health_path="/health"

    def _operation(self, capability: str) -> dict[str, Any]:
        operations=self.manifest.metadata.get("operations",{})
        spec=operations.get(capability)
        if not isinstance(spec,dict):
            raise ConnectorError(f"no mapped operation for capability: {capability}")
        method=str(spec.get("method","POST")).upper()
        if method not in _ALLOWED_METHODS:
            raise ConnectorError("mapped connector method is not allowed")
        path=str(spec.get("path","") or "")
        if not path.startswith("/"):
            raise ConnectorError("mapped connector path must be absolute")
        return {**spec,"method":method,"path":path}

    def _auth_headers(self) -> dict[str,str]:
        headers=dict(self.manifest.connector.headers)
        secret_ref=self.manifest.connector.secret_ref
        if secret_ref:
            secret=get_secret(f"connector:{self.manifest.id}",secret_ref) or os.getenv(secret_ref)
            if not secret: raise ConnectorError(f"connector secret not found: {secret_ref}")
            mode=str(self.manifest.connector.options.get("auth","bearer"))
            if mode == "bearer": headers.setdefault("Authorization",f"Bearer {secret}")
            elif mode == "header":
                name=str(self.manifest.connector.options.get("auth_header","X-API-Key"))
                headers.setdefault(name,secret)
            else: raise ConnectorError("unsupported mapped connector auth mode")
        return headers

    def _client_args(self) -> tuple[str,dict[str,Any]]:
        base=self.manifest.connector.base_url
        if not base: raise ConnectorError("mapped HTTP connector requires base_url")
        require_allowed_target(base)
        opts=self.manifest.connector.options
        verify_tls=bool(opts.get("verify_tls",True))
        if settings.environment == "production" and not verify_tls:
            raise ConnectorError("TLS verification cannot be disabled in production")
        kwargs:dict[str,Any]={"timeout":settings.http_timeout_seconds,"verify":verify_tls}
        uds=opts.get("unix_socket")
        if uds:
            if settings.production and not bool(opts.get("allow_local_socket",False)):
                raise ConnectorError("local Unix socket access requires allow_local_socket=true in production")
            kwargs["transport"]=httpx.AsyncHTTPTransport(uds=str(uds))
        return base.rstrip("/"),kwargs

    async def _call(self, method: str, path: str, *, parameters: dict[str,Any]|None=None, body: Any=None) -> dict[str,Any]:
        base,kwargs=self._client_args(); headers=self._auth_headers()
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                response=await client.request(method,base+path,params=parameters,json=body,headers=headers)
                response.raise_for_status()
                if not response.content: return {"ok":True,"status_code":response.status_code}
                ctype=response.headers.get("content-type","")
                data=response.json() if "json" in ctype else {"text":response.text}
        except (httpx.HTTPError,ValueError) as exc:
            raise ConnectorError(f"mapped HTTP connector failed: {exc}") from exc
        return data if isinstance(data,dict) else {"data":data}

    async def execute(self, capability: str, parameters: dict[str,Any], dry_run: bool=False) -> dict[str,Any]:
        spec=self._operation(capability)
        if dry_run: return {"ok":True,"dry_run":True,"operation":{"method":spec["method"],"path":spec["path"]}}
        path=_render(spec["path"],parameters)
        body=parameters if bool(spec.get("send_body",spec["method"] in {"POST","PUT","PATCH"})) else None
        query=parameters if bool(spec.get("send_query",spec["method"] == "GET")) else None
        result=await self._call(spec["method"],path,parameters=query,body=body)
        return {"ok":True,"remote":result}

    async def health(self) -> dict[str,Any]:
        path=str(self.manifest.connector.options.get("health_path",self.default_health_path))
        try: return {"ok":True,"remote":await self._call("GET",path)}
        except Exception as exc: return {"ok":False,"error":str(exc)}
