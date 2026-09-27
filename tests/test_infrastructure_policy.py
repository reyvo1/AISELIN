from app.models import ServerPlannedAction


def _org(client, auth, oid="org-infra"):
    r = client.post("/api/v1/organizations", headers=auth, json={"id": oid, "name": oid, "slug": oid})
    assert r.status_code == 200, r.text


def _agent(client, auth, caps, aid="ubuntu-infra", org="org-infra"):
    r = client.post(
        "/api/v1/agents", headers=auth,
        json={"id": aid, "organization_id": org, "name": aid, "kind": "server", "capabilities": caps},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _agent_headers(agent):
    return {"Authorization": "Bearer " + agent["token"]}


def test_infrastructure_default_levels_and_approval_queue(client, auth):
    _org(client, auth)
    agent = _agent(client, auth, ["service.status", "service.stop"])

    read = client.post(
        "/api/v1/infrastructure/actions", headers=auth,
        json={"agent_id": agent["id"], "capability": "service.status", "payload": {"service_name": "netmon.service"}, "reason": "inspect"},
    )
    assert read.status_code == 200, read.text
    assert read.json()["status"] == "queued" and read.json()["policy"] == "read"

    stop = client.post(
        "/api/v1/infrastructure/actions", headers=auth,
        json={"agent_id": agent["id"], "capability": "service.stop", "payload": {"service_name": "netmon.service"}, "reason": "maintenance"},
    )
    assert stop.status_code == 200, stop.text
    body = stop.json()
    assert body["status"] == "approval_required"

    pending = client.get("/api/v1/infrastructure/approvals?status=pending", headers=auth).json()
    assert any(x["id"] == body["approval_id"] for x in pending)

    approved = client.post(f"/api/v1/infrastructure/approvals/{body['approval_id']}/approve", headers=auth)
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["status"] == "queued"

    claimed = client.post("/api/v1/agent/tasks/claim", headers=_agent_headers(agent)).json()["task"]
    # First task is the read action, followed by the approved stop action.
    assert claimed["capability"] == "service.status"
    client.post(f"/api/v1/agent/tasks/{claimed['id']}/complete", headers=_agent_headers(agent), json={"status": "success", "result": {"active": True}})
    claimed2 = client.post("/api/v1/agent/tasks/claim", headers=_agent_headers(agent)).json()["task"]
    assert claimed2["capability"] == "service.stop"


def test_infrastructure_custom_forbidden_and_constraints(client, auth):
    _org(client, auth)
    agent = _agent(client, auth, ["service.restart"])
    p = client.post(
        "/api/v1/infrastructure/policies", headers=auth,
        json={
            "organization_id": "org-infra", "capability": "service.restart", "level": "controlled",
            "constraints": {"required_parameters": ["service_name"], "allowed_values": {"service_name": ["netmon.service"]}},
        },
    )
    assert p.status_code == 200, p.text

    denied_value = client.post(
        "/api/v1/infrastructure/actions", headers=auth,
        json={"agent_id": agent["id"], "capability": "service.restart", "payload": {"service_name": "mysql.service"}, "reason": "wrong target"},
    )
    assert denied_value.status_code == 200
    assert denied_value.json()["status"] == "blocked"

    p2 = client.post(
        "/api/v1/infrastructure/policies", headers=auth,
        json={"organization_id": "org-infra", "capability": "service.restart", "level": "forbidden", "constraints": {}},
    )
    assert p2.status_code == 200, p2.text
    blocked = client.post(
        "/api/v1/infrastructure/actions", headers=auth,
        json={"agent_id": agent["id"], "capability": "service.restart", "payload": {"service_name": "netmon.service"}, "reason": "policy block"},
    ).json()
    assert blocked["status"] == "blocked" and "forbidden" in blocked["message"]


def test_server_session_waits_for_infrastructure_approval_then_resumes(client, auth, monkeypatch):
    from app.core.config import settings

    _org(client, auth)
    agent = _agent(client, auth, ["system.status", "service.restart", "service.status"])
    p = client.post(
        "/api/v1/infrastructure/policies", headers=auth,
        json={"organization_id": "org-infra", "capability": "service.restart", "level": "approval", "constraints": {}},
    )
    assert p.status_code == 200, p.text

    plans = [
        ServerPlannedAction(action="service_restart", parameters={"service_name": "netmon.service"}, rationale="recover"),
        ServerPlannedAction(action="service_status", parameters={"service_name": "netmon.service"}, rationale="verify"),
        ServerPlannedAction(action="finish", parameters={}, rationale="recovered"),
    ]

    async def fake_plan(objective, node, context):
        return plans.pop(0)

    monkeypatch.setattr("app.ai.router.plan_server_with_router", fake_plan)
    old = settings.ai_provider
    object.__setattr__(settings, "ai_provider", "router")
    try:
        session = client.post(
            "/api/v1/server/sessions", headers=auth,
            json={"agent_id": agent["id"], "objective": "netmon mati, perbaiki dan verifikasi"},
        ).json()
        first = client.post("/api/v1/agent/tasks/claim", headers=_agent_headers(agent)).json()["task"]
        assert first["capability"] == "system.status"
        client.post(f"/api/v1/agent/tasks/{first['id']}/complete", headers=_agent_headers(agent), json={"status": "success", "result": {"ok": True}})

        waiting = client.get(f"/api/v1/server/sessions/{session['id']}", headers=auth).json()
        assert waiting["status"] == "waiting_approval"
        approval_id = waiting["result"]["infrastructure_approval_id"]
        assert client.post("/api/v1/agent/tasks/claim", headers=_agent_headers(agent)).json()["task"] is None

        approved = client.post(f"/api/v1/infrastructure/approvals/{approval_id}/approve", headers=auth)
        assert approved.status_code == 200, approved.text
        resumed = client.get(f"/api/v1/server/sessions/{session['id']}", headers=auth).json()
        assert resumed["status"] == "running" and resumed["current_task_id"]

        restart = client.post("/api/v1/agent/tasks/claim", headers=_agent_headers(agent)).json()["task"]
        assert restart["capability"] == "service.restart"
        client.post(f"/api/v1/agent/tasks/{restart['id']}/complete", headers=_agent_headers(agent), json={"status": "success", "result": {"ok": True}})
        verify = client.post("/api/v1/agent/tasks/claim", headers=_agent_headers(agent)).json()["task"]
        assert verify["capability"] == "service.status"
        client.post(f"/api/v1/agent/tasks/{verify['id']}/complete", headers=_agent_headers(agent), json={"status": "success", "result": {"stdout": "active"}})
        done = client.get(f"/api/v1/server/sessions/{session['id']}", headers=auth).json()
        assert done["status"] == "success" and done["phase"] == "complete"
    finally:
        object.__setattr__(settings, "ai_provider", old)


def test_rejecting_infrastructure_approval_stops_server_session(client, auth, monkeypatch):
    from app.core.config import settings

    _org(client, auth)
    agent = _agent(client, auth, ["system.status", "service.restart"])
    client.post(
        "/api/v1/infrastructure/policies", headers=auth,
        json={"organization_id": "org-infra", "capability": "service.restart", "level": "approval", "constraints": {}},
    )

    async def fake_plan(objective, node, context):
        return ServerPlannedAction(action="service_restart", parameters={"service_name": "netmon.service"}, rationale="recover")

    monkeypatch.setattr("app.ai.router.plan_server_with_router", fake_plan)
    old = settings.ai_provider
    object.__setattr__(settings, "ai_provider", "router")
    try:
        session = client.post("/api/v1/server/sessions", headers=auth, json={"agent_id": agent["id"], "objective": "recover"}).json()
        first = client.post("/api/v1/agent/tasks/claim", headers=_agent_headers(agent)).json()["task"]
        client.post(f"/api/v1/agent/tasks/{first['id']}/complete", headers=_agent_headers(agent), json={"status": "success", "result": {"ok": True}})
        waiting = client.get(f"/api/v1/server/sessions/{session['id']}", headers=auth).json()
        approval_id = waiting["result"]["infrastructure_approval_id"]
        rejected = client.post(f"/api/v1/infrastructure/approvals/{approval_id}/reject", headers=auth)
        assert rejected.status_code == 200, rejected.text
        final = client.get(f"/api/v1/server/sessions/{session['id']}", headers=auth).json()
        assert final["status"] == "rejected" and final["phase"] == "approval_rejected"
    finally:
        object.__setattr__(settings, "ai_provider", old)
