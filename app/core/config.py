from __future__ import annotations

import os
from dataclasses import dataclass


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in value.split(",") if x.strip())


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("AIOC_ENV", "development")
    database_path: str = os.getenv("AIOC_DATABASE_PATH", "./data/aioc.db")
    database_url_override: str = os.getenv("AIOC_DATABASE_URL", "")
    allowed_connector_hosts: tuple[str, ...] = _split_csv(os.getenv("AIOC_ALLOWED_CONNECTOR_HOSTS", "*"))
    http_timeout_seconds: float = float(os.getenv("AIOC_HTTP_TIMEOUT_SECONDS", "15"))
    owner_token: str = os.getenv("AIOC_OWNER_TOKEN", "change-me")
    event_token: str = os.getenv("AIOC_EVENT_TOKEN", "change-event-me")
    master_key: str = os.getenv("AIOC_MASTER_KEY", "development-master-key-change-me")
    ai_provider: str = os.getenv("AIOC_AI_PROVIDER", "rules")
    ai_base_url: str = os.getenv("AIOC_AI_BASE_URL", "")
    ai_api_key: str = os.getenv("AIOC_AI_API_KEY", "")
    ai_model: str = os.getenv("AIOC_AI_MODEL", "")
    ai_providers_json: str = os.getenv("AIOC_AI_PROVIDERS_JSON", "")
    ai_daily_request_limit: int = _int("AIOC_AI_DAILY_REQUEST_LIMIT", 500)
    ai_daily_token_limit: int = _int("AIOC_AI_DAILY_TOKEN_LIMIT", 1000000)
    ai_max_plan_actions: int = _int("AIOC_AI_MAX_PLAN_ACTIONS", 12)
    rate_limit_per_minute: int = _int("AIOC_RATE_LIMIT_PER_MINUTE", 600)
    max_request_bytes: int = _int("AIOC_MAX_REQUEST_BYTES", 1048576)
    webhook_tolerance_seconds: int = _int("AIOC_WEBHOOK_TOLERANCE_SECONDS", 300)
    circuit_failure_threshold: int = _int("AIOC_CIRCUIT_FAILURE_THRESHOLD", 3)
    circuit_reset_seconds: int = _int("AIOC_CIRCUIT_RESET_SECONDS", 120)
    worker_poll_seconds: int = _int("AIOC_WORKER_POLL_SECONDS", 5)
    worker_lease_seconds: int = _int("AIOC_WORKER_LEASE_SECONDS", 60)
    max_action_attempts: int = _int("AIOC_MAX_ACTION_ATTEMPTS", 3)
    oidc_enabled: bool = os.getenv("AIOC_OIDC_ENABLED", "false").lower() in {"1","true","yes","on"}
    oidc_issuer: str = os.getenv("AIOC_OIDC_ISSUER", "")
    oidc_audience: str = os.getenv("AIOC_OIDC_AUDIENCE", "")
    oidc_jwks_url: str = os.getenv("AIOC_OIDC_JWKS_URL", "")
    oidc_role_claim: str = os.getenv("AIOC_OIDC_ROLE_CLAIM", "aioc_role")
    oidc_org_claim: str = os.getenv("AIOC_OIDC_ORG_CLAIM", "aioc_org")
    oidc_principal_claim: str = os.getenv("AIOC_OIDC_PRINCIPAL_CLAIM", "sub")
    oidc_jwks_cache_seconds: int = _int("AIOC_OIDC_JWKS_CACHE_SECONDS", 300)
    max_queued_jobs_per_org: int = _int("AIOC_MAX_QUEUED_JOBS_PER_ORG", 10000)
    worker_heartbeat_seconds: int = _int("AIOC_WORKER_HEARTBEAT_SECONDS", 15)
    worker_stale_seconds: int = _int("AIOC_WORKER_STALE_SECONDS", 60)
    otel_enabled: bool = os.getenv("AIOC_OTEL_ENABLED", "false").lower() in {"1","true","yes","on"}
    otel_endpoint: str = os.getenv("AIOC_OTEL_ENDPOINT", "")
    otel_service_name: str = os.getenv("AIOC_OTEL_SERVICE_NAME", "aioc")
    agent_task_lease_seconds: int = _int("AIOC_AGENT_TASK_LEASE_SECONDS", 120)
    agent_stale_seconds: int = _int("AIOC_AGENT_STALE_SECONDS", 90)
    agent_max_task_attempts: int = _int("AIOC_AGENT_MAX_TASK_ATTEMPTS", 3)
    artifact_dir: str = os.getenv("AIOC_ARTIFACT_DIR", "./data/artifacts")
    artifact_max_bytes: int = _int("AIOC_ARTIFACT_MAX_BYTES", 536870912)
    artifact_retention_days: int = _int("AIOC_ARTIFACT_RETENTION_DAYS", 30)
    artifact_max_per_project: int = _int("AIOC_ARTIFACT_MAX_PER_PROJECT", 20)
    deployment_timeout_seconds: int = _int("AIOC_DEPLOYMENT_TIMEOUT_SECONDS", 1800)

    @property
    def production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return f"sqlite:///{self.database_path}"


settings = Settings()
