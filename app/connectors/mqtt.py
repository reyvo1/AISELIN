from __future__ import annotations
import asyncio, json, os
from typing import Any
from urllib.parse import urlparse
from app.connectors.base import BaseConnector, ConnectorError
from app.services.vault import get_secret
from app.connectors.security import require_allowed_target

class MQTTConnector(BaseConnector):
    def _target(self):
        url=self.manifest.connector.base_url
        if not url: raise ConnectorError("MQTT connector requires base_url")
        u=urlparse(url if "://" in url else "mqtt://"+url)
        if u.scheme not in {"mqtt","mqtts"} or not u.hostname: raise ConnectorError("invalid MQTT URL")
        return u
    def _op(self,capability):
        spec=self.manifest.metadata.get("operations",{}).get(capability)
        if not isinstance(spec,dict) or not spec.get("topic"): raise ConnectorError(f"no MQTT topic mapped for capability: {capability}")
        return spec
    async def execute(self, capability: str, parameters: dict[str,Any], dry_run: bool=False) -> dict[str,Any]:
        spec=self._op(capability); topic=str(spec["topic"])
        if dry_run: return {"ok":True,"dry_run":True,"topic":topic}
        try: import paho.mqtt.client as mqtt
        except ImportError as exc: raise ConnectorError("MQTT support requires paho-mqtt") from exc
        u=self._target(); require_allowed_target(self.manifest.connector.base_url or ""); secret=None
        if self.manifest.connector.secret_ref:
            ref=self.manifest.connector.secret_ref; secret=get_secret(f"connector:{self.manifest.id}",ref) or os.getenv(ref)
        username=self.manifest.connector.options.get("username")
        payload=json.dumps(parameters,separators=(",",":"),default=str)
        def publish():
            client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            if username: client.username_pw_set(str(username),secret)
            if u.scheme == "mqtts": client.tls_set()
            client.connect(u.hostname,u.port or (8883 if u.scheme=="mqtts" else 1883),keepalive=20)
            info=client.publish(topic,payload,qos=int(spec.get("qos",1)),retain=bool(spec.get("retain",False)))
            info.wait_for_publish(timeout=10); client.disconnect()
            if info.rc != mqtt.MQTT_ERR_SUCCESS: raise RuntimeError(f"MQTT publish failed rc={info.rc}")
        try: await asyncio.to_thread(publish)
        except Exception as exc: raise ConnectorError(f"MQTT connector failed: {exc}") from exc
        return {"ok":True,"topic":topic}
