"""Minimal dependency-light connector SDK for Python production applications."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Handler = Callable[[dict[str, Any], bool], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass
class ConnectorRuntime:
    application_id: str
    handlers: dict[str, Handler] = field(default_factory=dict)
    verifiers: dict[str, Handler] = field(default_factory=dict)
    rollback_handlers: dict[str, Handler] = field(default_factory=dict)

    def capability(self, name: str, handler: Handler, *, verifier: Handler | None = None, rollback: Handler | None = None) -> None:
        if not name or name in self.handlers:
            raise ValueError("capability name must be unique")
        self.handlers[name] = handler
        if verifier: self.verifiers[name] = verifier
        if rollback: self.rollback_handlers[name] = rollback

    def capabilities(self) -> dict[str, Any]:
        return {"ok": True, "application_id": self.application_id, "capabilities": sorted(self.handlers)}

    async def execute(self, capability: str, parameters: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        handler = self.handlers.get(capability)
        if not handler: raise KeyError(f"unsupported capability: {capability}")
        value = handler(parameters, dry_run)
        if hasattr(value, "__await__"): value = await value
        if not isinstance(value, dict): raise TypeError("connector handlers must return dictionaries")
        return value

    async def verify(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        handler = self.verifiers.get(capability)
        if not handler: return {"ok": True, "mode": "default"}
        value = handler(payload, False)
        if hasattr(value, "__await__"): value = await value
        return value

    async def rollback(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        handler = self.rollback_handlers.get(capability)
        if not handler: return {"ok": False, "supported": False}
        value = handler(payload, False)
        if hasattr(value, "__await__"): value = await value
        return value


def sign_event(secret: str, event: dict[str, Any], timestamp: int | None = None) -> tuple[bytes, dict[str, str]]:
    ts = str(timestamp or int(time.time()))
    body = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return body, {"Content-Type": "application/json", "X-AIOC-Timestamp": ts, "X-AIOC-Signature": f"sha256={signature}"}
