from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class PolicyLevel(StrEnum):
    READ = "read"
    AUTO = "auto"
    CONTROLLED = "controlled"
    APPROVAL = "approval"
    FORBIDDEN = "forbidden"


class Capability(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = ""
    default_policy: PolicyLevel = PolicyLevel.READ
    input_schema: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    retry: dict[str, Any] = Field(default_factory=dict)
    rollback_capability: str | None = None
    risk: Literal["low", "medium", "high", "critical"] = "low"


class ConnectorConfig(BaseModel):
    type: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    base_url: str | None = None
    secret_ref: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    version: str = "1"


class ApplicationManifest(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$")
    name: str = Field(min_length=2, max_length=100)
    type: str = Field(min_length=2, max_length=100)
    environment: Literal["development", "staging", "production"] = "production"
    organization_id: str = "global"
    business_id: str | None = None
    location_id: str | None = None
    connector: ConnectorConfig
    capabilities: list[Capability]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def capability_names_unique(cls, values: list[Capability]) -> list[Capability]:
        names = [x.name for x in values]
        if len(names) != len(set(names)):
            raise ValueError("capability names must be unique")
        return values


class PolicyCreate(BaseModel):
    app_id: str | None = None
    capability: str
    level: PolicyLevel
    constraints: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    app_id: str
    capability: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str = "user request"
    actor: str = "owner"
    dry_run: bool = False
    idempotency_key: str | None = Field(default=None, max_length=160)


class ActionResult(BaseModel):
    status: Literal["success", "blocked", "approval_required", "failed", "dry_run", "queued"]
    run_id: str
    app_id: str
    capability: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None


class CommandRequest(BaseModel):
    command: str = Field(min_length=2, max_length=4000)
    actor: str = "owner"
    dry_run: bool = False


class PlannedAction(BaseModel):
    app_id: str
    capability: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class RuleCreate(BaseModel):
    name: str
    trigger_type: Literal["event", "schedule"]
    trigger: dict[str, Any]
    condition: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any]
    cooldown_seconds: int = Field(default=0, ge=0, le=604800)
    enabled: bool = True


class EventIn(BaseModel):
    source_app_id: str | None = None
    event_type: str = Field(min_length=2, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class OrganizationCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$")
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class PrincipalCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$")
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["human", "service", "agent"] = "human"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApiKeyCreate(BaseModel):
    principal_id: str
    organization_id: str = "global"
    role: Literal["owner", "admin", "operator", "viewer"] = "viewer"
    label: str = "api-key"
    expires_at: str | None = None


class ResourceCreate(BaseModel):
    organization_id: str = "global"
    app_id: str | None = None
    resource_type: str
    external_id: str
    name: str
    status: str = "unknown"
    attributes: dict[str, Any] = Field(default_factory=dict)


class ResourceEdgeCreate(BaseModel):
    organization_id: str = "global"
    from_resource_id: str
    to_resource_id: str
    relation: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class IncidentResolve(BaseModel):
    note: str = "resolved by operator"


class QueueAction(BaseModel):
    request: ActionRequest
    max_attempts: int = Field(default=3, ge=1, le=10)


class BusinessCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$")
    organization_id: str = "global"
    name: str = Field(min_length=2, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocationCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$")
    organization_id: str = "global"
    business_id: str | None = None
    name: str = Field(min_length=2, max_length=120)
    timezone: str = "UTC"
    currency: str | None = None
    country_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationChannelCreate(BaseModel):
    organization_id: str = "global"
    name: str = Field(min_length=2, max_length=120)
    channel_type: Literal["log", "webhook", "telegram", "slack", "email", "whatsapp"] = "log"
    config: dict[str, Any] = Field(default_factory=dict)
    secret_name: str | None = None


class WorkflowStep(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
    app_id: str
    capability: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    on_failure: Literal["stop", "continue", "rollback"] = "stop"
    approval_override: bool = False


class WorkflowCreate(BaseModel):
    organization_id: str = "global"
    name: str = Field(min_length=2, max_length=160)
    description: str = ""
    steps: list[WorkflowStep] = Field(min_length=1, max_length=100)
    enabled: bool = True
    max_parallel_steps: int = Field(default=1, ge=1, le=16)

    @field_validator("steps")
    @classmethod
    def validate_graph(cls, steps: list[WorkflowStep]) -> list[WorkflowStep]:
        ids=[x.id for x in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow step ids must be unique")
        known=set(ids)
        for step in steps:
            if step.id in step.depends_on:
                raise ValueError(f"workflow step {step.id} cannot depend on itself")
            unknown=set(step.depends_on)-known
            if unknown:
                raise ValueError(f"workflow step {step.id} has unknown dependencies: {sorted(unknown)}")
        return steps


class WorkflowRunRequest(BaseModel):
    reason: str = "manual workflow run"
    inputs: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1, le=86400)


class MemoryCreate(BaseModel):
    organization_id: str = "global"
    app_id: str | None = None
    namespace: str = Field(default="operations", min_length=1, max_length=80)
    key: str = Field(min_length=1, max_length=160)
    value: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=50)


class KnowledgeDocumentCreate(BaseModel):
    organization_id: str = "global"
    app_id: str | None = None
    title: str = Field(min_length=2, max_length=240)
    content: str = Field(min_length=1, max_length=200000)
    source: str = Field(default="manual", max_length=240)
    tags: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)



class AgentNodeCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$")
    organization_id: str = "global"
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["local", "server", "edge"]
    capabilities: list[str] = Field(default_factory=list, max_length=200)
    labels: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentHeartbeat(BaseModel):
    version: str = Field(default="unknown", max_length=80)
    capabilities: list[str] = Field(default_factory=list, max_length=200)
    status: Literal["online", "busy", "degraded"] = "online"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTaskResult(BaseModel):
    status: Literal["success", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=4000)


class ProjectProfileCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$")
    organization_id: str = "global"
    name: str = Field(min_length=2, max_length=160)
    source_agent_id: str
    repo_path: str = Field(min_length=1, max_length=1200)
    default_branch: str = Field(default="main", max_length=160)
    runtime: str = Field(default="auto", max_length=80)
    build_steps: list[list[str]] = Field(default_factory=list, max_length=50)
    test_steps: list[list[str]] = Field(default_factory=list, max_length=50)
    artifact_excludes: list[str] = Field(default_factory=lambda: [".git", ".venv", "node_modules", "__pycache__", ".env", ".env.*", "*.pem", "*.key"], max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("build_steps", "test_steps")
    @classmethod
    def validate_steps(cls, steps: list[list[str]]) -> list[list[str]]:
        for step in steps:
            if not step or len(step) > 64 or any(not isinstance(x, str) or not x or len(x) > 1000 for x in step):
                raise ValueError("command steps must be non-empty argv arrays")
        return steps


class DeploymentTargetCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}$")
    project_id: str
    organization_id: str = "global"
    server_agent_id: str
    environment: Literal["development", "staging", "production"] = "production"
    target_root: str = Field(min_length=1, max_length=1200)
    service_type: Literal["none", "systemd", "docker-compose"] = "none"
    service_name: str | None = Field(default=None, max_length=200)
    health_url: str | None = Field(default=None, max_length=2000)
    public_host: str | None = Field(default=None, max_length=253)
    local_port: int | None = Field(default=None, ge=1, le=65535)
    publish_mode: Literal["none", "nginx"] = "none"
    config: dict[str, Any] = Field(default_factory=dict)


class DeploymentRequest(BaseModel):
    reason: str = Field(default="owner deployment request", max_length=1000)
    allow_dirty: bool = False
    publish: bool = False


class OperatorCommandRequest(BaseModel):
    command: str = Field(min_length=2, max_length=4000)
    organization_id: str | None = None
    dry_run: bool = False


class AgentTaskCreate(BaseModel):
    agent_id: str
    capability: str = Field(min_length=2, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=10)


class OperatorPlannedAction(BaseModel):
    tool: Literal["project.deploy", "project.inspect", "project.develop", "server.diagnose", "agent.task", "application.action"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class DeveloperSessionRequest(BaseModel):
    project_id: str
    objective: str = Field(min_length=3, max_length=8000)
    max_iterations: int = Field(default=8, ge=1, le=30)
    auto_commit: bool = False
    auto_deploy_target_id: str | None = None


class DeveloperPlannedAction(BaseModel):
    action: Literal["read", "search", "patch", "test", "finish", "fail"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class ServerSessionRequest(BaseModel):
    agent_id: str
    objective: str = Field(min_length=3, max_length=8000)
    max_iterations: int = Field(default=8, ge=1, le=30)


class ServerPlannedAction(BaseModel):
    action: Literal[
        "system_status", "process_list", "network_sockets", "network_routes", "firewall_status",
        "package_updates", "package_security_updates", "package_apply_updates",
        "service_status", "service_logs", "service_restart", "docker_list", "docker_logs", "docker_restart",
        "finish", "fail"
    ]
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class InfrastructurePolicyCreate(BaseModel):
    organization_id: str = "global"
    capability: str = Field(min_length=2, max_length=120)
    level: PolicyLevel
    constraints: dict[str, Any] = Field(default_factory=dict)


class InfrastructureActionRequest(BaseModel):
    agent_id: str
    capability: str = Field(min_length=2, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="operator request", max_length=1000)
