from __future__ import annotations
import os, shlex
from typing import Any
from urllib.parse import urlparse
from app.connectors.base import BaseConnector, ConnectorError
from app.core.config import settings
from app.services.vault import get_secret
from app.connectors.security import require_allowed_target

class SSHConnector(BaseConnector):
    def _operation(self,capability):
        spec=self.manifest.metadata.get("operations",{}).get(capability)
        if not isinstance(spec,dict) or not spec.get("command"): raise ConnectorError(f"no SSH command mapped for capability: {capability}")
        return spec
    def _render(self,template,parameters):
        out=str(template)
        for k,v in parameters.items(): out=out.replace("{"+k+"}",shlex.quote(str(v)))
        if "{" in out or "}" in out: raise ConnectorError("SSH command has unresolved parameters")
        return out
    async def execute(self, capability: str, parameters: dict[str,Any], dry_run: bool=False) -> dict[str,Any]:
        spec=self._operation(capability); cmd=self._render(spec["command"],parameters)
        if dry_run: return {"ok":True,"dry_run":True,"command_template":spec["command"]}
        try: import asyncssh
        except ImportError as exc: raise ConnectorError("SSH support requires asyncssh") from exc
        require_allowed_target(self.manifest.connector.base_url or ""); u=urlparse(self.manifest.connector.base_url or "")
        if u.scheme != "ssh" or not u.hostname: raise ConnectorError("SSH connector requires ssh://user@host:port base_url")
        secret=None
        if self.manifest.connector.secret_ref:
            ref=self.manifest.connector.secret_ref; secret=get_secret(f"connector:{self.manifest.id}",ref) or os.getenv(ref)
        known_hosts=self.manifest.connector.options.get("known_hosts")
        if settings.environment == "production" and not known_hosts: raise ConnectorError("SSH known_hosts is required in production")
        try:
            async with asyncssh.connect(u.hostname,port=u.port or 22,username=u.username,password=secret,known_hosts=known_hosts) as conn:
                result=await conn.run(cmd,check=False,timeout=float(spec.get("timeout",30)))
        except Exception as exc: raise ConnectorError(f"SSH connector failed: {exc}") from exc
        return {"ok":result.exit_status==0,"exit_status":result.exit_status,"stdout":result.stdout[-10000:],"stderr":result.stderr[-10000:]}
