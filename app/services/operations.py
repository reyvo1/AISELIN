from __future__ import annotations

from app.core.db import connect
from app.services.incidents import list_incidents
from app.services.jobs import list_jobs
from app.services.registry import registry
from app.services.resources import list_resources
from app.services.workflows import list_runs


def summary(organization_id: str | None=None) -> dict:
    apps=registry.list(organization_id)
    incidents=list_incidents("open",organization_id)
    jobs=list_jobs(limit=500,organization_id=organization_id)
    resources=list_resources(organization_id)
    workflows=list_runs(organization_id,limit=500)
    with connect() as conn:
        if organization_id is None:
            audit_count=conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()["n"]
            action_failures=conn.execute("SELECT COUNT(*) AS n FROM audit_events WHERE decision='failed'").fetchone()["n"]
            agent_count=conn.execute("SELECT COUNT(*) AS n FROM agent_nodes").fetchone()["n"]
            project_count=conn.execute("SELECT COUNT(*) AS n FROM project_profiles WHERE enabled=1").fetchone()["n"]
            deployment_count=conn.execute("SELECT COUNT(*) AS n FROM deployment_runs WHERE status IN ('running','rolling_back')").fetchone()["n"]
            developer_count=conn.execute("SELECT COUNT(*) AS n FROM developer_sessions WHERE status='running'").fetchone()["n"]
            server_session_count=conn.execute("SELECT COUNT(*) AS n FROM server_sessions WHERE status='running'").fetchone()["n"]
        else:
            audit_count=conn.execute("SELECT COUNT(*) AS n FROM audit_events WHERE organization_id=?",(organization_id,)).fetchone()["n"]
            action_failures=conn.execute("SELECT COUNT(*) AS n FROM audit_events WHERE organization_id=? AND decision='failed'",(organization_id,)).fetchone()["n"]
            agent_count=conn.execute("SELECT COUNT(*) AS n FROM agent_nodes WHERE organization_id=?",(organization_id,)).fetchone()["n"]
            project_count=conn.execute("SELECT COUNT(*) AS n FROM project_profiles WHERE organization_id=? AND enabled=1",(organization_id,)).fetchone()["n"]
            deployment_count=conn.execute("SELECT COUNT(*) AS n FROM deployment_runs WHERE organization_id=? AND status IN ('running','rolling_back')",(organization_id,)).fetchone()["n"]
            developer_count=conn.execute("SELECT COUNT(*) AS n FROM developer_sessions WHERE organization_id=? AND status='running'",(organization_id,)).fetchone()["n"]
            server_session_count=conn.execute("SELECT COUNT(*) AS n FROM server_sessions WHERE organization_id=? AND status='running'",(organization_id,)).fetchone()["n"]
    return {
        "applications":len(apps),"open_incidents":len(incidents),
        "queued_jobs":sum(1 for j in jobs if j["status"]=="queued"),
        "failed_jobs":sum(1 for j in jobs if j["status"]=="failed"),
        "running_workflows":sum(1 for r in workflows if r["status"] in {"queued","running","waiting_approval"}),
        "failed_workflows":sum(1 for r in workflows if r["status"]=="failed"),
        "resources":len(resources),"audit_events":int(audit_count),"action_failures":int(action_failures),
        "agents":int(agent_count),"projects":int(project_count),"active_deployments":int(deployment_count),"active_developer_sessions":int(developer_count),"active_server_sessions":int(server_session_count)
    }
