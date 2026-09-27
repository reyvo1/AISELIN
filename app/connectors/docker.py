from app.connectors.mapped_http import MappedHttpConnector

class DockerConnector(MappedHttpConnector):
    default_health_path="/_ping"
