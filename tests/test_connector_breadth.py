from __future__ import annotations

import pytest

from app.connectors.factory import build_connector
from app.connectors.registry import available_connectors
from app.models import ApplicationManifest


def manifest(kind: str, **extra):
    connector={"type":kind,"base_url":extra.pop("base_url","https://ops.example.test"),"options":extra.pop("options",{})}
    return ApplicationManifest.model_validate({
        "id":f"app-{kind.replace('_','-')}","name":f"App {kind}","type":"ops","environment":"staging",
        "connector":connector,
        "capabilities":[{"name":"service.restart","default_policy":"controlled"}],
        "metadata":{"operations":{"service.restart":extra.pop("operation",{"method":"POST","path":"/services/{name}/restart"})}},
        **extra,
    })


def test_standard_connector_catalog_is_available():
    expected={"simulator","rest","graphql","mapped_http","docker","kubernetes","mqtt","ssh","snmp"}
    assert expected.issubset(set(available_connectors()))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["mapped_http","docker","kubernetes"])
async def test_http_transport_connectors_support_safe_dry_run(kind):
    c=build_connector(manifest(kind))
    result=await c.execute("service.restart",{"name":"api"},dry_run=True)
    assert result["ok"] is True and result["dry_run"] is True
    assert "api" not in result["operation"]["path"]  # dry run exposes template, not expanded remote call


@pytest.mark.asyncio
async def test_mqtt_connector_dry_run_needs_declared_topic_not_network():
    m=manifest("mqtt",base_url="mqtt://broker.example.test",operation={"topic":"ops/restart","qos":1})
    c=build_connector(m)
    result=await c.execute("service.restart",{"name":"api"},dry_run=True)
    assert result == {"ok":True,"dry_run":True,"topic":"ops/restart"}


@pytest.mark.asyncio
async def test_ssh_connector_dry_run_only_uses_declared_command_template():
    m=manifest("ssh",base_url="ssh://ops@server.example.test:22",operation={"command":"systemctl restart {name}"})
    c=build_connector(m)
    result=await c.execute("service.restart",{"name":"api;rm -rf /"},dry_run=True)
    assert result["ok"] is True
    assert result["command_template"] == "systemctl restart {name}"
    assert "rm -rf" not in result["command_template"]


@pytest.mark.asyncio
async def test_mapped_connector_rejects_undeclared_capability():
    c=build_connector(manifest("mapped_http"))
    with pytest.raises(Exception,match="no mapped operation"):
        await c.execute("root.shell",{},dry_run=True)


def test_network_connector_allowlist_applies_to_ssh_and_mqtt(monkeypatch):
    from app.core.config import settings
    from app.connectors.security import require_allowed_target
    old=settings.allowed_connector_hosts
    object.__setattr__(settings,'allowed_connector_hosts',('trusted.example',))
    try:
        assert require_allowed_target('ssh://ops@trusted.example:22')=='trusted.example'
        with pytest.raises(Exception,match='not in AIOC_ALLOWED_CONNECTOR_HOSTS'):
            require_allowed_target('mqtt://evil.example:1883')
    finally:
        object.__setattr__(settings,'allowed_connector_hosts',old)


@pytest.mark.asyncio
async def test_snmp_connector_dry_run_is_declarative():
    m=manifest("snmp",base_url="snmp://router.example.test:161",operation={"operation":"get","oid":"1.3.6.1.2.1.1.3.0"})
    c=build_connector(m)
    result=await c.execute("service.restart",{},dry_run=True)
    assert result["ok"] is True and result["oid"]=="1.3.6.1.2.1.1.3.0"

@pytest.mark.asyncio
async def test_snmp_write_requires_explicit_opt_in():
    m=manifest("snmp",base_url="snmp://router.example.test:161",operation={"operation":"set","oid":"1.2.3"})
    c=build_connector(m)
    with pytest.raises(Exception,match="allow_write"):
        await c.execute("service.restart",{"value":1},dry_run=True)
