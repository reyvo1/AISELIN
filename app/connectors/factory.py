from __future__ import annotations

from app.connectors.base import BaseConnector
from app.connectors.registry import connector_class
from app.models import ApplicationManifest


def build_connector(manifest: ApplicationManifest) -> BaseConnector:
    return connector_class(manifest.connector.type)(manifest)
