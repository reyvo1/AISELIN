from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from app.core.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_limits (
  key_id TEXT NOT NULL,
  window_id TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(key_id,window_id)
);

CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  connector_type TEXT NOT NULL,
  environment TEXT NOT NULL,
  organization_id TEXT NOT NULL DEFAULT 'global',
  business_id TEXT,
  location_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  manifest_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
  id TEXT PRIMARY KEY,
  app_id TEXT,
  capability TEXT NOT NULL,
  level TEXT NOT NULL,
  constraints_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policies_app_cap ON policies(app_id, capability);
CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  app_id TEXT,
  capability TEXT,
  action TEXT NOT NULL,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  request_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  organization_id TEXT NOT NULL DEFAULT 'global'
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts DESC);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  app_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  actor TEXT NOT NULL,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL,
  approved_by TEXT,
  note TEXT,
  organization_id TEXT NOT NULL DEFAULT 'global'
);
CREATE TABLE IF NOT EXISTS rules (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  trigger_type TEXT NOT NULL,
  trigger_json TEXT NOT NULL,
  condition_json TEXT NOT NULL,
  action_json TEXT NOT NULL,
  cooldown_seconds INTEGER NOT NULL DEFAULT 0,
  last_fired_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  organization_id TEXT NOT NULL DEFAULT 'global'
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  source_app_id TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  organization_id TEXT NOT NULL DEFAULT 'global'
);
CREATE TABLE IF NOT EXISTS action_runs (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT UNIQUE,
  app_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_action_runs_key ON action_runs(idempotency_key);

CREATE TABLE IF NOT EXISTS organizations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  metadata_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS businesses (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  business_id TEXT,
  name TEXT NOT NULL,
  timezone TEXT NOT NULL,
  currency TEXT,
  country_code TEXT,
  metadata_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS principals (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  role TEXT NOT NULL,
  label TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT,
  revoked_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS vault_secrets (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  name TEXT NOT NULL,
  nonce TEXT NOT NULL,
  ciphertext TEXT NOT NULL,
  version INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(scope,name)
);

CREATE TABLE IF NOT EXISTS connector_states (
  app_id TEXT PRIMARY KEY,
  lifecycle_status TEXT NOT NULL,
  version TEXT NOT NULL,
  secret_version INTEGER NOT NULL DEFAULT 0,
  last_validated_at TEXT,
  last_health_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_receipts (
  id TEXT PRIMARY KEY,
  app_id TEXT NOT NULL,
  signature TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(app_id,signature)
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  available_at TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  organization_id TEXT NOT NULL DEFAULT 'global'
);
CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(status,available_at);
CREATE TABLE IF NOT EXISTS distributed_locks (
  name TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  lease_until TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS circuit_breakers (
  app_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  state TEXT NOT NULL,
  failure_count INTEGER NOT NULL DEFAULT 0,
  threshold INTEGER NOT NULL DEFAULT 3,
  opened_at TEXT,
  reset_after_seconds INTEGER NOT NULL DEFAULT 120,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(app_id, capability)
);

CREATE TABLE IF NOT EXISTS action_verifications (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  app_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  status TEXT NOT NULL,
  verification_json TEXT NOT NULL,
  rollback_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resources (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  app_id TEXT,
  resource_type TEXT NOT NULL,
  external_id TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  attributes_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(organization_id,resource_type,external_id)
);
CREATE TABLE IF NOT EXISTS resource_edges (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  from_resource_id TEXT NOT NULL,
  to_resource_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  attributes_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(from_resource_id,to_resource_id,relation)
);

CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  app_id TEXT,
  severity TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  source_event_id TEXT,
  metadata_json TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status,severity,opened_at);

CREATE TABLE IF NOT EXISTS notification_channels (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL,
  channel_type TEXT NOT NULL,
  config_json TEXT NOT NULL,
  secret_name TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL,
  incident_id TEXT,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage (
  id TEXT PRIMARY KEY,
  provider_name TEXT NOT NULL,
  model TEXT NOT NULL,
  day TEXT NOT NULL,
  requests INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  estimated_cost REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  UNIQUE(provider_name,model,day)
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_day ON ai_usage(day,provider_name);

CREATE TABLE IF NOT EXISTS workflows (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  definition_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflows_org ON workflows(organization_id,enabled);
CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  status TEXT NOT NULL,
  actor TEXT NOT NULL,
  reason TEXT NOT NULL,
  inputs_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  workflow_version INTEGER NOT NULL DEFAULT 1,
  workflow_definition_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs ON workflow_runs(organization_id,status,started_at);
CREATE TABLE IF NOT EXISTS workflow_step_runs (
  id TEXT PRIMARY KEY,
  workflow_run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  app_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_step_runs ON workflow_step_runs(workflow_run_id,step_id);
CREATE TABLE IF NOT EXISTS operational_memory (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  app_id TEXT,
  namespace TEXT NOT NULL,
  memory_key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(organization_id,app_id,namespace,memory_key)
);
CREATE INDEX IF NOT EXISTS idx_memory_scope ON operational_memory(organization_id,app_id,namespace);
CREATE TABLE IF NOT EXISTS knowledge_documents (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  app_id TEXT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  source TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_scope ON knowledge_documents(organization_id,app_id,updated_at);
CREATE TABLE IF NOT EXISTS worker_nodes (
  id TEXT PRIMARY KEY,
  hostname TEXT NOT NULL,
  pid INTEGER NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  last_heartbeat TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worker_heartbeat ON worker_nodes(status,last_heartbeat);

CREATE TABLE IF NOT EXISTS agent_nodes (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'offline',
  capabilities_json TEXT NOT NULL,
  labels_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  version TEXT NOT NULL DEFAULT 'unknown',
  last_heartbeat TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_nodes_org ON agent_nodes(organization_id,status,kind);
CREATE TABLE IF NOT EXISTS agent_tasks (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  available_at TEXT NOT NULL,
  lease_until TEXT,
  result_json TEXT NOT NULL,
  error TEXT,
  correlation_type TEXT,
  correlation_id TEXT,
  artifact_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_claim ON agent_tasks(agent_id,status,available_at);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_correlation ON agent_tasks(correlation_type,correlation_id);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  project_id TEXT,
  kind TEXT NOT NULL,
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  storage_path TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_org_project ON artifacts(organization_id,project_id,created_at);
CREATE TABLE IF NOT EXISTS project_profiles (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL,
  source_agent_id TEXT NOT NULL,
  repo_path TEXT NOT NULL,
  default_branch TEXT NOT NULL,
  runtime TEXT NOT NULL,
  build_steps_json TEXT NOT NULL,
  test_steps_json TEXT NOT NULL,
  artifact_excludes_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_org ON project_profiles(organization_id,enabled);
CREATE TABLE IF NOT EXISTS deployment_targets (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  server_agent_id TEXT NOT NULL,
  environment TEXT NOT NULL,
  target_root TEXT NOT NULL,
  service_type TEXT NOT NULL,
  service_name TEXT,
  health_url TEXT,
  public_host TEXT,
  local_port INTEGER,
  publish_mode TEXT NOT NULL DEFAULT 'none',
  config_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_targets_project ON deployment_targets(project_id,environment,enabled);
CREATE TABLE IF NOT EXISTS deployment_runs (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL,
  phase TEXT NOT NULL,
  allow_dirty INTEGER NOT NULL DEFAULT 0,
  publish_requested INTEGER NOT NULL DEFAULT 0,
  source_revision TEXT,
  artifact_id TEXT,
  previous_release_id TEXT,
  current_task_id TEXT,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_deploy_runs_project ON deployment_runs(project_id,target_id,created_at);
CREATE TABLE IF NOT EXISTS project_releases (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  deployment_run_id TEXT NOT NULL,
  revision TEXT,
  artifact_id TEXT,
  release_path TEXT,
  status TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  activated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_releases_target ON project_releases(target_id,status,created_at);
CREATE TABLE IF NOT EXISTS developer_sessions (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  objective TEXT NOT NULL,
  status TEXT NOT NULL,
  phase TEXT NOT NULL,
  iteration INTEGER NOT NULL DEFAULT 0,
  max_iterations INTEGER NOT NULL DEFAULT 8,
  auto_commit INTEGER NOT NULL DEFAULT 0,
  auto_deploy_target_id TEXT,
  baseline_revision TEXT,
  current_task_id TEXT,
  context_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_developer_sessions_org ON developer_sessions(organization_id,project_id,created_at);
CREATE TABLE IF NOT EXISTS infrastructure_policies (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  level TEXT NOT NULL,
  constraints_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(organization_id,capability)
);
CREATE INDEX IF NOT EXISTS idx_infra_policy_scope ON infrastructure_policies(organization_id,capability);
CREATE TABLE IF NOT EXISTS infrastructure_approvals (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  actor TEXT NOT NULL,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL,
  approved_by TEXT,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_infra_approvals_scope ON infrastructure_approvals(organization_id,status,created_at);

CREATE TABLE IF NOT EXISTS server_sessions (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  objective TEXT NOT NULL,
  status TEXT NOT NULL,
  phase TEXT NOT NULL,
  iteration INTEGER NOT NULL DEFAULT 0,
  max_iterations INTEGER NOT NULL DEFAULT 8,
  current_task_id TEXT,
  context_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_server_sessions_org ON server_sessions(organization_id,agent_id,created_at);

CREATE TABLE IF NOT EXISTS operator_runs (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  command TEXT NOT NULL,
  plan_json TEXT NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operator_runs_org ON operator_runs(organization_id,created_at);

CREATE TABLE IF NOT EXISTS telemetry_counters (
  metric_key TEXT PRIMARY KEY,
  value INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
"""

_engine: Engine | None = None
_engine_url: str | None = None


def _get_engine() -> Engine:
    global _engine, _engine_url
    url = settings.database_url
    if _engine is None or _engine_url != url:
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if url.startswith("sqlite:"):
            kwargs["connect_args"] = {"check_same_thread": False}
            db_path = url.replace("sqlite:///", "", 1)
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, **kwargs)
        _engine_url = url
    return _engine


def _translate_qmarks(sql: str, params: tuple[Any, ...] | list[Any]) -> tuple[str, dict[str, Any]]:
    values = list(params)
    index = 0
    parts: list[str] = []
    for segment in sql.split("?"):
        parts.append(segment)
        if index < len(values):
            parts.append(f":p{index}")
            index += 1
    if index != len(values):
        raise ValueError("SQL parameter count mismatch")
    return "".join(parts), {f"p{i}": value for i, value in enumerate(values)}


class ResultProxy:
    def __init__(self, result):
        self._result = result
        self.rowcount = result.rowcount

    def fetchone(self):
        row = self._result.mappings().fetchone()
        return row

    def fetchall(self):
        return self._result.mappings().fetchall()


class DbConnection:
    def __init__(self, connection: Connection):
        self._connection = connection

    @property
    def dialect(self) -> str:
        return self._connection.dialect.name

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | dict[str, Any] = ()) -> ResultProxy:
        if isinstance(params, dict):
            statement, bound = sql, params
        else:
            statement, bound = _translate_qmarks(sql, params)
        result = self._connection.execute(text(statement), bound)
        return ResultProxy(result)


def init_db(path: str | None = None) -> None:
    # path remains for M1 compatibility; database URL is authoritative in M2+.
    engine = _get_engine()
    with engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.execute(text("PRAGMA journal_mode=WAL"))
        for statement in [s.strip() for s in SCHEMA.split(";") if s.strip()]:
            conn.execute(text(statement))
    _bootstrap_global_org()
    from app.core.migrations import record_current_schema
    record_current_schema()


def _bootstrap_global_org() -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """INSERT INTO organizations(id,name,slug,metadata_json,enabled,created_at,updated_at)
               VALUES(?,?,?,?,1,?,?) ON CONFLICT(id) DO NOTHING""",
            ("global", "Global", "global", "{}", now, now),
        )


@contextmanager
def connect(path: str | None = None) -> Iterator[DbConnection]:
    engine = _get_engine()
    with engine.begin() as raw:
        if raw.dialect.name == "sqlite":
            raw.execute(text("PRAGMA foreign_keys=ON"))
        yield DbConnection(raw)


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(data: str) -> Any:
    return json.loads(data)


def reset_engine_for_tests() -> None:
    global _engine, _engine_url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None

TEST_TABLES = [
    "rate_limits","schema_migrations","notifications","notification_channels","ai_usage","action_verifications","circuit_breakers","jobs","distributed_locks","webhook_receipts","connector_states",
    "resource_edges","resources","incidents","vault_secrets","api_keys","principals","locations","businesses","organizations",
    "infrastructure_approvals","infrastructure_policies","server_sessions","developer_sessions","operator_runs","project_releases","deployment_runs","deployment_targets","project_profiles","artifacts","agent_tasks","agent_nodes",
    "worker_nodes","workflow_step_runs","workflow_runs","workflows","knowledge_documents","operational_memory","telemetry_counters","action_runs","events","rules","approvals","audit_events","policies","applications",
]


def reset_database_for_tests() -> None:
    """Destructive helper for isolated CI/test databases only."""
    engine = _get_engine()
    with engine.begin() as conn:
        for table in TEST_TABLES:
            suffix = " CASCADE" if conn.dialect.name == "postgresql" else ""
            conn.execute(text(f"DROP TABLE IF EXISTS {table}{suffix}"))
