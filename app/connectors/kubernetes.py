from app.connectors.mapped_http import MappedHttpConnector

class KubernetesConnector(MappedHttpConnector):
    default_health_path="/version"
