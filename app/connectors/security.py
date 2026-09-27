from __future__ import annotations
from urllib.parse import urlparse
from app.core.config import settings
from app.connectors.base import ConnectorError


def target_host(url: str) -> str:
    value=url if '://' in url else 'tcp://'+url
    return (urlparse(value).hostname or '').strip().lower()


def require_allowed_target(url: str) -> str:
    host=target_host(url)
    if not host: raise ConnectorError('connector target host is invalid')
    allowed=settings.allowed_connector_hosts
    if '*' in allowed:
        if settings.production: raise ConnectorError('wildcard connector host allowlist is forbidden in production')
        return host
    if not any(host == item.lower() or host.endswith('.'+item.lower()) for item in allowed):
        raise ConnectorError('connector host is not in AIOC_ALLOWED_CONNECTOR_HOSTS')
    return host
