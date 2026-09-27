from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from app.connectors.base import BaseConnector, ConnectorError
from app.connectors.security import require_allowed_target
from app.services.vault import get_secret


class SNMPConnector(BaseConnector):
    """SNMP v2c baseline. GET is default; SET requires explicit allow_write plus capability mapping."""
    def _target(self):
        raw=self.manifest.connector.base_url or ""
        if not raw: raise ConnectorError("SNMP connector requires base_url")
        url=raw if "://" in raw else "snmp://"+raw
        require_allowed_target(url)
        u=urlparse(url)
        if u.scheme not in {"snmp","snmp+udp"} or not u.hostname: raise ConnectorError("invalid SNMP URL")
        return u.hostname,u.port or 161

    def _op(self,capability: str) -> dict[str,Any]:
        spec=self.manifest.metadata.get("operations",{}).get(capability)
        if not isinstance(spec,dict) or not spec.get("oid"): raise ConnectorError(f"no SNMP OID mapped for capability: {capability}")
        mode=str(spec.get("operation","get")).lower()
        if mode not in {"get","set"}: raise ConnectorError("SNMP operation must be get or set")
        if mode=="set" and not bool(self.manifest.connector.options.get("allow_write",False)):
            raise ConnectorError("SNMP SET requires connector.options.allow_write=true")
        return {**spec,"operation":mode}

    async def execute(self, capability: str, parameters: dict[str,Any], dry_run: bool=False) -> dict[str,Any]:
        spec=self._op(capability); host,port=self._target(); oid=str(spec["oid"])
        if dry_run:return {"ok":True,"dry_run":True,"operation":spec["operation"],"oid":oid,"target":host}
        ref=self.manifest.connector.secret_ref
        community=(get_secret(f"connector:{self.manifest.id}",ref) or os.getenv(ref)) if ref else None
        if not community: raise ConnectorError("SNMP community secret is required")
        try:
            from pysnmp.hlapi.asyncio import SnmpEngine,CommunityData,UdpTransportTarget,ContextData,ObjectType,ObjectIdentity,get_cmd,set_cmd
            from pysnmp.proto.rfc1902 import OctetString,Integer32
        except ImportError as exc: raise ConnectorError("SNMP support requires pysnmp") from exc
        try:
            target=await UdpTransportTarget.create((host,port),timeout=float(spec.get("timeout",3)),retries=int(spec.get("retries",1)))
            args=(SnmpEngine(),CommunityData(community,mpModel=1),target,ContextData())
            if spec["operation"]=="get":
                err_ind,err_status,err_index,var_binds=await get_cmd(*args,ObjectType(ObjectIdentity(oid)))
            else:
                value=parameters.get("value")
                typed=Integer32(int(value)) if spec.get("value_type")=="integer" else OctetString(str(value))
                err_ind,err_status,err_index,var_binds=await set_cmd(*args,ObjectType(ObjectIdentity(oid),typed))
            if err_ind or err_status: raise ConnectorError(f"SNMP error: {err_ind or err_status.prettyPrint()}")
            return {"ok":True,"values":[{"oid":str(name),"value":value.prettyPrint()} for name,value in var_binds]}
        except ConnectorError: raise
        except Exception as exc: raise ConnectorError(f"SNMP connector failed: {exc}") from exc
