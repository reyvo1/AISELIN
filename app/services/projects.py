from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import re
from pathlib import PurePosixPath

from app.core.db import connect, dumps, loads
from app.models import DeploymentTargetCreate, ProjectProfileCreate
from app.services.agents import get_agent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_project(row) -> dict[str, Any]:
    item = dict(row)
    item["build_steps"] = loads(item.pop("build_steps_json"))
    item["test_steps"] = loads(item.pop("test_steps_json"))
    item["artifact_excludes"] = loads(item.pop("artifact_excludes_json"))
    item["metadata"] = loads(item.pop("metadata_json"))
    item["enabled"] = bool(item["enabled"])
    return item


def create_project(item: ProjectProfileCreate) -> dict[str, Any]:
    source = get_agent(item.source_agent_id)
    if not source or source["organization_id"] != item.organization_id:
        raise ValueError("source agent not found in organization")
    if source["kind"] not in {"local", "edge"}:
        raise ValueError("source agent must be local or edge")
    required = {"repo.inspect", "repo.prepare_release"}
    if not required.issubset(set(source["capabilities"])):
        raise ValueError(f"source agent missing required capabilities: {sorted(required-set(source['capabilities']))}")
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO project_profiles(id,organization_id,name,source_agent_id,repo_path,default_branch,runtime,build_steps_json,test_steps_json,artifact_excludes_json,metadata_json,enabled,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,source_agent_id=excluded.source_agent_id,repo_path=excluded.repo_path,default_branch=excluded.default_branch,runtime=excluded.runtime,build_steps_json=excluded.build_steps_json,test_steps_json=excluded.test_steps_json,artifact_excludes_json=excluded.artifact_excludes_json,metadata_json=excluded.metadata_json,enabled=1,updated_at=excluded.updated_at""",
            (item.id,item.organization_id,item.name,item.source_agent_id,item.repo_path,item.default_branch,item.runtime,dumps(item.build_steps),dumps(item.test_steps),dumps(item.artifact_excludes),dumps(item.metadata),now,now),
        )
    return get_project(item.id) or {"id": item.id}


def get_project(project_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM project_profiles WHERE id=?", (project_id,)).fetchone()
    return _decode_project(row) if row else None


def list_projects(organization_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM project_profiles WHERE enabled=1 ORDER BY name").fetchall() if organization_id is None else conn.execute("SELECT * FROM project_profiles WHERE organization_id=? AND enabled=1 ORDER BY name", (organization_id,)).fetchall()
    return [_decode_project(row) for row in rows]


def _decode_target(row) -> dict[str, Any]:
    item = dict(row)
    item["config"] = loads(item.pop("config_json"))
    item["enabled"] = bool(item["enabled"])
    return item


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _validated_deployment_config(config: dict[str, Any]) -> dict[str, Any]:
    config = dict(config or {})
    environment = config.get("environment") or {}
    secret_env = config.get("secret_env") or {}
    if not isinstance(environment, dict) or not isinstance(secret_env, dict):
        raise ValueError("deployment config environment and secret_env must be objects")
    for key, value in environment.items():
        if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
            raise ValueError(f"invalid environment variable name: {key}")
        if not isinstance(value, (str, int, float, bool)) or "\x00" in str(value):
            raise ValueError(f"invalid environment value for {key}")
    for key, secret_name in secret_env.items():
        if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
            raise ValueError(f"invalid secret environment variable name: {key}")
        if not isinstance(secret_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", secret_name):
            raise ValueError(f"invalid deployment secret reference for {key}")
    relative = str(config.get("env_relative_path") or "shared/.env")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("env_relative_path must be a safe relative path")
    config["environment"] = {str(k): str(v) for k, v in environment.items()}
    config["secret_env"] = {str(k): str(v) for k, v in secret_env.items()}
    config["env_relative_path"] = relative
    config["link_env_to_release"] = bool(config.get("link_env_to_release", True))
    return config


def create_target(item: DeploymentTargetCreate) -> dict[str, Any]:
    project = get_project(item.project_id)
    if not project or project["organization_id"] != item.organization_id:
        raise ValueError("project not found in organization")
    agent = get_agent(item.server_agent_id)
    if not agent or agent["organization_id"] != item.organization_id:
        raise ValueError("server agent not found in organization")
    if agent["kind"] not in {"server", "edge"}:
        raise ValueError("deployment target agent must be server or edge")
    config = _validated_deployment_config(item.config)
    required = {"deploy.stage", "deploy.activate", "deploy.health", "deploy.rollback"}
    if config.get("environment") or config.get("secret_env"):
        required.add("deploy.configure")
        allowed = set(agent.get("metadata", {}).get("allowed_secrets") or [])
        requested = set((config.get("secret_env") or {}).values())
        missing_secrets = requested - allowed
        if missing_secrets:
            raise ValueError(f"server agent is not granted deployment secrets: {sorted(missing_secrets)}")
    if item.publish_mode == "nginx":
        required.add("publish.nginx")
    missing = required - set(agent["capabilities"])
    if missing:
        raise ValueError(f"server agent missing required capabilities: {sorted(missing)}")
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO deployment_targets(id,project_id,organization_id,server_agent_id,environment,target_root,service_type,service_name,health_url,public_host,local_port,publish_mode,config_json,enabled,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
               ON CONFLICT(id) DO UPDATE SET project_id=excluded.project_id,organization_id=excluded.organization_id,server_agent_id=excluded.server_agent_id,environment=excluded.environment,target_root=excluded.target_root,service_type=excluded.service_type,service_name=excluded.service_name,health_url=excluded.health_url,public_host=excluded.public_host,local_port=excluded.local_port,publish_mode=excluded.publish_mode,config_json=excluded.config_json,enabled=1,updated_at=excluded.updated_at""",
            (item.id,item.project_id,item.organization_id,item.server_agent_id,item.environment,item.target_root,item.service_type,item.service_name,item.health_url,item.public_host,item.local_port,item.publish_mode,dumps(config),now,now),
        )
    return get_target(item.id) or {"id": item.id}


def get_target(target_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM deployment_targets WHERE id=?", (target_id,)).fetchone()
    return _decode_target(row) if row else None


def list_targets(project_id: str | None = None, organization_id: str | None = None) -> list[dict[str, Any]]:
    where = ["enabled=1"]; params: list[Any] = []
    if project_id is not None: where.append("project_id=?"); params.append(project_id)
    if organization_id is not None: where.append("organization_id=?"); params.append(organization_id)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM deployment_targets WHERE " + " AND ".join(where) + " ORDER BY environment,id", tuple(params)).fetchall()
    return [_decode_target(row) for row in rows]


def set_project_enabled(project_id: str, enabled: bool) -> dict[str, Any] | None:
    with connect() as conn:
        result=conn.execute("UPDATE project_profiles SET enabled=?,updated_at=? WHERE id=?", (1 if enabled else 0,_now(),project_id))
    if result.rowcount != 1:
        return None
    with connect() as conn:
        row=conn.execute("SELECT * FROM project_profiles WHERE id=?",(project_id,)).fetchone()
    return _decode_project(row) if row else None


def set_target_enabled(target_id: str, enabled: bool) -> dict[str, Any] | None:
    with connect() as conn:
        result=conn.execute("UPDATE deployment_targets SET enabled=?,updated_at=? WHERE id=?", (1 if enabled else 0,_now(),target_id))
    if result.rowcount != 1:
        return None
    with connect() as conn:
        row=conn.execute("SELECT * FROM deployment_targets WHERE id=?",(target_id,)).fetchone()
    return _decode_target(row) if row else None
