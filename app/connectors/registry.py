from __future__ import annotations

from importlib.metadata import entry_points
from typing import Type

from app.connectors.base import BaseConnector, ConnectorError

_CONNECTORS: dict[str, Type[BaseConnector]] = {}
_LOADED=False


def register_connector(name: str, connector: Type[BaseConnector], *, replace: bool=False) -> None:
    key=name.strip().lower()
    if not key: raise ValueError("connector name is required")
    if key in _CONNECTORS and not replace: raise ValueError(f"connector already registered: {key}")
    _CONNECTORS[key]=connector


def _load_builtins() -> None:
    from app.connectors.simulator import SimulatorConnector
    from app.connectors.rest import RestConnector
    from app.connectors.graphql import GraphQLConnector
    from app.connectors.mapped_http import MappedHttpConnector
    from app.connectors.docker import DockerConnector
    from app.connectors.kubernetes import KubernetesConnector
    from app.connectors.mqtt import MQTTConnector
    from app.connectors.ssh import SSHConnector
    from app.connectors.snmp import SNMPConnector
    for name,cls in {"simulator":SimulatorConnector,"rest":RestConnector,"graphql":GraphQLConnector,"mapped_http":MappedHttpConnector,"docker":DockerConnector,"kubernetes":KubernetesConnector,"mqtt":MQTTConnector,"ssh":SSHConnector,"snmp":SNMPConnector}.items():
        _CONNECTORS.setdefault(name,cls)


def load_plugins() -> None:
    global _LOADED
    if _LOADED: return
    _load_builtins()
    try:
        eps=entry_points(group="aioc.connectors")
    except TypeError:
        eps=entry_points().get("aioc.connectors",[])
    for ep in eps:
        try:
            cls=ep.load()
            if isinstance(cls,type) and issubclass(cls,BaseConnector): _CONNECTORS.setdefault(ep.name.lower(),cls)
        except Exception:
            # A broken third-party plugin cannot prevent AIOC from starting.
            continue
    _LOADED=True


def connector_class(name: str) -> Type[BaseConnector]:
    load_plugins(); cls=_CONNECTORS.get(name.lower())
    if not cls: raise ConnectorError(f"unsupported connector: {name}")
    return cls


def available_connectors() -> list[str]:
    load_plugins(); return sorted(_CONNECTORS)
