from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.db import connect, dumps, loads
from app.models import DeploymentRequest
from app.services.agents import enqueue_agent_task, get_agent_task
from app.services.audit import audit
from app.services.projects import get_project, get_target
from app.services.locks import acquire as acquire_lock, release as release_lock
from app.services.incidents import open_incident
from app.core.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()




def _lock_owner(run_id: str) -> str: return f"deployment:{run_id}"
def _project_lock(project_id: str) -> str: return f"engineering:project:{project_id}"
def _target_lock(target_id: str) -> str: return f"engineering:target:{target_id}"

def _renew_locks(run: dict[str, Any]) -> bool:
    owner=_lock_owner(run["id"]); lease=max(60,settings.deployment_timeout_seconds)
    return acquire_lock(_project_lock(run["project_id"]),owner,lease) and acquire_lock(_target_lock(run["target_id"]),owner,lease)

def _release_locks(run: dict[str, Any]) -> None:
    owner=_lock_owner(run["id"]); release_lock(_project_lock(run["project_id"]),owner); release_lock(_target_lock(run["target_id"]),owner)

def _decode_run(row) -> dict[str, Any]:
    item = dict(row)
    item["result"] = loads(item.pop("result_json"))
    item["allow_dirty"] = bool(item["allow_dirty"])
    item["publish_requested"] = bool(item["publish_requested"])
    return item


def get_deployment(run_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM deployment_runs WHERE id=?", (run_id,)).fetchone()
    return _decode_run(row) if row else None


def list_deployments(organization_id: str | None = None, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if organization_id is not None: where.append("organization_id=?"); params.append(organization_id)
    if project_id is not None: where.append("project_id=?"); params.append(project_id)
    sql = "SELECT * FROM deployment_runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_decode_run(row) for row in rows]


def _decode_release(row) -> dict[str, Any]:
    item = dict(row); item["metadata"] = loads(item.pop("metadata_json")); return item


def list_releases(target_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM project_releases WHERE target_id=? ORDER BY created_at DESC LIMIT ?", (target_id, max(1,min(limit,200)))).fetchall()
    return [_decode_release(row) for row in rows]


def _active_release(target_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM project_releases WHERE target_id=? AND status='active' ORDER BY activated_at DESC LIMIT 1", (target_id,)).fetchone()
    return _decode_release(row) if row else None


def _merge_result(run: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    result = dict(run.get("result") or {})
    result[key] = value
    return result


def _update_run(run_id: str, *, status: str | None = None, phase: str | None = None, current_task_id: str | None | object = ..., source_revision: str | None | object = ..., artifact_id: str | None | object = ..., result: dict[str, Any] | None = None, finished: bool = False) -> dict[str, Any]:
    run = get_deployment(run_id)
    if not run: raise ValueError("deployment not found")
    values = {
        "status": status if status is not None else run["status"],
        "phase": phase if phase is not None else run["phase"],
        "current_task_id": run["current_task_id"] if current_task_id is ... else current_task_id,
        "source_revision": run["source_revision"] if source_revision is ... else source_revision,
        "artifact_id": run["artifact_id"] if artifact_id is ... else artifact_id,
        "result": result if result is not None else run["result"],
        "finished_at": _now() if finished else run["finished_at"],
    }
    with connect() as conn:
        conn.execute(
            "UPDATE deployment_runs SET status=?,phase=?,current_task_id=?,source_revision=?,artifact_id=?,result_json=?,updated_at=?,finished_at=? WHERE id=?",
            (values["status"],values["phase"],values["current_task_id"],values["source_revision"],values["artifact_id"],dumps(values["result"]),_now(),values["finished_at"],run_id),
        )
    return get_deployment(run_id) or run


def _queue(run: dict[str, Any], agent_id: str, capability: str, payload: dict[str, Any], phase: str) -> dict[str, Any]:
    if not _renew_locks(run): raise RuntimeError("deployment engineering lock was lost")
    task = enqueue_agent_task(agent_id, capability, payload, organization_id=run["organization_id"], correlation_type="deployment", correlation_id=run["id"])
    return _update_run(run["id"], phase=phase, current_task_id=task["id"])


def start_deployment(target_id: str, request: DeploymentRequest, actor: str) -> dict[str, Any]:
    target = get_target(target_id)
    if not target or not target["enabled"]: raise ValueError("deployment target not found or disabled")
    project = get_project(target["project_id"])
    if not project or not project["enabled"]: raise ValueError("project not found or disabled")
    if project["organization_id"] != target["organization_id"]: raise ValueError("project/target organization mismatch")
    run_id = str(uuid4()); now = _now(); previous = _active_release(target_id)
    owner=_lock_owner(run_id); lease=max(60,settings.deployment_timeout_seconds)
    if not acquire_lock(_project_lock(project["id"]),owner,lease): raise ValueError("project is busy with another engineering/deployment operation")
    if not acquire_lock(_target_lock(target_id),owner,lease):
        release_lock(_project_lock(project["id"]),owner)
        raise ValueError("deployment target is busy with another deployment")
    with connect() as conn:
        conn.execute(
            """INSERT INTO deployment_runs(id,organization_id,project_id,target_id,actor,reason,status,phase,allow_dirty,publish_requested,source_revision,artifact_id,previous_release_id,current_task_id,result_json,created_at,updated_at,finished_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id,target["organization_id"],project["id"],target_id,actor,request.reason,"running","source_inspect",1 if request.allow_dirty else 0,1 if request.publish else 0,None,None,previous["id"] if previous else None,None,dumps({}),now,now,None),
        )
    run = get_deployment(run_id)
    if not run:
        release_lock(_project_lock(project["id"]),owner); release_lock(_target_lock(target_id),owner)
        raise RuntimeError("deployment creation failed")
    try:
        run = _queue(run, project["source_agent_id"], "repo.inspect", {"project_id": project["id"], "repo_path": project["repo_path"], "default_branch": project["default_branch"]}, "source_inspect")
    except Exception:
        _release_locks(run); raise
    audit(actor=actor,app_id=None,capability=None,action="deployment.start",decision="queued",reason=request.reason,request={"target_id":target_id,"allow_dirty":request.allow_dirty,"publish":request.publish},result={"run_id":run_id},organization_id=target["organization_id"])
    return run


def _create_release(run: dict[str, Any], release_path: str | None = None) -> dict[str, Any]:
    release_id = str(uuid4()); now = _now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO project_releases(id,organization_id,project_id,target_id,deployment_run_id,revision,artifact_id,release_path,status,metadata_json,created_at,activated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (release_id,run["organization_id"],run["project_id"],run["target_id"],run["id"],run["source_revision"],run["artifact_id"],release_path,"staging",dumps({}),now),
        )
    return {"id": release_id, "release_path": release_path}


def _release_for_run(run_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM project_releases WHERE deployment_run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
    return _decode_release(row) if row else None


def _finalize_success(run: dict[str, Any], final_result: dict[str, Any]) -> dict[str, Any]:
    release = _release_for_run(run["id"])
    now = _now()
    if not release: raise RuntimeError("deployment release record missing")
    with connect() as conn:
        conn.execute("UPDATE project_releases SET status='superseded' WHERE target_id=? AND status='active' AND id<>?", (run["target_id"], release["id"]))
        conn.execute("UPDATE project_releases SET status='active',activated_at=? WHERE id=?", (now, release["id"]))
    result = _merge_result(run,"final",final_result)
    completed = _update_run(run["id"], status="success", phase="complete", current_task_id=None, result=result, finished=True)
    _release_locks(completed)
    audit(actor=run["actor"],app_id=None,capability=None,action="deployment.complete",decision="success",reason=run["reason"],request={"run_id":run["id"],"target_id":run["target_id"]},result=result,organization_id=run["organization_id"])
    return completed


def _queue_rollback(run: dict[str, Any], failure: dict[str, Any]) -> dict[str, Any]:
    target = get_target(run["target_id"])
    if not target: return _fail(run, failure)
    previous = None
    if run.get("previous_release_id"):
        with connect() as conn:
            row = conn.execute("SELECT * FROM project_releases WHERE id=?", (run["previous_release_id"],)).fetchone()
        previous = _decode_release(row) if row else None
    if not previous or not previous.get("release_path"):
        return _fail(run, {**failure,"rollback":"unavailable: no previous active release"})
    result = _merge_result(run,"failure",failure)
    run = _update_run(run["id"], status="rolling_back", result=result)
    return _queue(run,target["server_agent_id"],"deploy.rollback",{
        "project_id":run["project_id"],"target_root":target["target_root"],"previous_release_path":previous["release_path"],
        "service_type":target["service_type"],"service_name":target["service_name"],
    },"rollback")


def _fail(run: dict[str, Any], failure: dict[str, Any]) -> dict[str, Any]:
    release = _release_for_run(run["id"])
    if release:
        with connect() as conn: conn.execute("UPDATE project_releases SET status='failed' WHERE id=?", (release["id"],))
    result = _merge_result(run,"failure",failure)
    failed = _update_run(run["id"],status="failed",phase="failed",current_task_id=None,result=result,finished=True)
    _release_locks(failed)
    audit(actor=run["actor"],app_id=None,capability=None,action="deployment.complete",decision="failed",reason=run["reason"],request={"run_id":run["id"]},result=result,organization_id=run["organization_id"])
    open_incident(organization_id=run["organization_id"], severity="high", title=f"Deployment failed: {run['project_id']}", description=str(failure)[:8000], fingerprint=f"deployment:{run['target_id']}", metadata={"run_id":run["id"],"target_id":run["target_id"],"project_id":run["project_id"],"failure":failure})
    return failed


def advance_from_agent_task(task_id: str) -> dict[str, Any] | None:
    task = get_agent_task(task_id)
    if not task or task.get("correlation_type") != "deployment" or not task.get("correlation_id"):
        return None
    run = get_deployment(task["correlation_id"])
    if not run: return None
    if run["current_task_id"] != task_id:
        return run
    # A failed attempt that was requeued is not terminal and must not advance the state machine.
    if task["status"] == "queued":
        return run
    if task["status"] == "failed":
        failure = {"phase":run["phase"],"task_id":task_id,"error":task.get("error") or task.get("result")}
        if run["phase"] in {"deploy_activate","deploy_health","publish"}:
            return _queue_rollback(run,failure)
        if run["phase"] == "rollback":
            result=_merge_result(run,"rollback",{"status":"failed","task":task})
            failed=_update_run(run["id"],status="rollback_failed",phase="rollback_failed",current_task_id=None,result=result,finished=True)
            _release_locks(failed)
            open_incident(organization_id=run["organization_id"], severity="critical", title=f"Deployment rollback failed: {run['project_id']}", description=str(result)[:8000], fingerprint=f"deployment-rollback:{run['target_id']}", metadata={"run_id":run["id"],"target_id":run["target_id"],"project_id":run["project_id"],"result":result})
            return failed
        return _fail(run,failure)
    if task["status"] != "success":
        return run

    project = get_project(run["project_id"]); target = get_target(run["target_id"])
    if not project or not target: return _fail(run,{"error":"project or target disappeared during deployment"})
    phase = run["phase"]; result = task.get("result") or {}
    accumulated = _merge_result(run,phase,result)
    run = _update_run(run["id"],result=accumulated)

    if phase == "source_inspect":
        dirty = bool(result.get("dirty"))
        revision = str(result.get("revision") or "unknown")
        if dirty and not run["allow_dirty"]:
            return _fail(run,{"phase":phase,"error":"source repository is dirty and deployment does not allow dirty source","revision":revision})
        run = _update_run(run["id"],source_revision=revision)
        return _queue(run,project["source_agent_id"],"repo.prepare_release",{
            "project_id":project["id"],"repo_path":project["repo_path"],"revision":revision,
            "build_steps":project["build_steps"],"test_steps":project["test_steps"],"artifact_excludes":project["artifact_excludes"],
        },"source_prepare")

    if phase == "source_prepare":
        if not task.get("artifact_id"):
            return _fail(run,{"phase":phase,"error":"release task succeeded without an uploaded artifact"})
        run = _update_run(run["id"],artifact_id=task["artifact_id"])
        release = _create_release(run)
        return _queue(run,target["server_agent_id"],"deploy.stage",{
            "project_id":project["id"],"artifact_id":task["artifact_id"],"release_id":release["id"],"target_root":target["target_root"],
        },"deploy_stage")

    if phase == "deploy_stage":
        release = _release_for_run(run["id"])
        release_path = str(result.get("release_path") or "")
        if not release or not release_path:
            return _fail(run,{"phase":phase,"error":"stage did not return a release path"})
        with connect() as conn: conn.execute("UPDATE project_releases SET release_path=? WHERE id=?", (release_path,release["id"]))
        config=target.get("config") or {}
        if config.get("environment") or config.get("secret_env"):
            return _queue(run,target["server_agent_id"],"deploy.configure",{
                "project_id":project["id"],"target_root":target["target_root"],
                "env_relative_path":config.get("env_relative_path") or "shared/.env",
                "environment":config.get("environment") or {},"secret_env":config.get("secret_env") or {},
            },"deploy_configure")
        return _queue(run,target["server_agent_id"],"deploy.activate",{
            "project_id":project["id"],"release_id":release["id"],"release_path":release_path,"target_root":target["target_root"],
            "service_type":target["service_type"],"service_name":target["service_name"],
        },"deploy_activate")

    if phase == "deploy_configure":
        release = _release_for_run(run["id"])
        if not release or not release.get("release_path"):
            return _fail(run,{"phase":phase,"error":"deployment release path missing before activation"})
        config=target.get("config") or {}
        env_file=str(result.get("env_file") or "")
        if not env_file:
            return _fail(run,{"phase":phase,"error":"deployment configuration did not return env_file"})
        return _queue(run,target["server_agent_id"],"deploy.activate",{
            "project_id":project["id"],"release_id":release["id"],"release_path":release["release_path"],"target_root":target["target_root"],
            "service_type":target["service_type"],"service_name":target["service_name"],
            "env_file":env_file,"link_env_to_release":bool(config.get("link_env_to_release",True)),
        },"deploy_activate")

    if phase == "deploy_activate":
        return _queue(run,target["server_agent_id"],"deploy.health",{
            "project_id":project["id"],"health_url":target["health_url"],"service_type":target["service_type"],"service_name":target["service_name"],
        },"deploy_health")

    if phase == "deploy_health":
        if result.get("ok") is False:
            return _queue_rollback(run,{"phase":phase,"error":"post-deploy health verification failed","result":result})
        if run["publish_requested"] and target["publish_mode"] == "nginx":
            if not target.get("public_host") or not target.get("local_port"):
                return _queue_rollback(run,{"phase":"publish","error":"nginx publishing requires public_host and local_port"})
            return _queue(run,target["server_agent_id"],"publish.nginx",{
                "project_id":project["id"],"public_host":target["public_host"],"local_port":target["local_port"],"service_name":target["service_name"],
            },"publish")
        return _finalize_success(run,{"health":result,"published":False})

    if phase == "publish":
        if result.get("ok") is False:
            return _queue_rollback(run,{"phase":phase,"error":"publish verification failed","result":result})
        return _finalize_success(run,{"publish":result,"published":True})

    if phase == "rollback":
        release = _release_for_run(run["id"])
        if release:
            with connect() as conn: conn.execute("UPDATE project_releases SET status='rolled_back' WHERE id=?", (release["id"],))
        if run.get("previous_release_id"):
            with connect() as conn: conn.execute("UPDATE project_releases SET status='active',activated_at=COALESCE(activated_at,?) WHERE id=?", (_now(),run["previous_release_id"]))
        rollback_result = _merge_result(run,"rollback",result)
        rolled=_update_run(run["id"],status="rolled_back",phase="rolled_back",current_task_id=None,result=rollback_result,finished=True)
        _release_locks(rolled); return rolled

    return run
