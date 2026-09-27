from app.models import ServerPlannedAction


def _org(client,auth,oid='org-server'):
    r=client.post('/api/v1/organizations',headers=auth,json={'id':oid,'name':oid,'slug':oid}); assert r.status_code==200,r.text


def _agent(client,auth,caps,aid='ubuntu-main',org='org-server'):
    r=client.post('/api/v1/agents',headers=auth,json={'id':aid,'organization_id':org,'name':aid,'kind':'server','capabilities':caps}); assert r.status_code==200,r.text; return r.json()


def _h(agent): return {'Authorization':'Bearer '+agent['token']}


def test_server_session_bounded_diagnose_recover_verify(client,auth,monkeypatch):
    from app.core.config import settings
    _org(client,auth)
    agent=_agent(client,auth,['system.status','service.status','service.logs','service.restart'])
    plans=[
        ServerPlannedAction(action='service_logs',parameters={'service_name':'netmon.service','lines':100},rationale='inspect failure evidence'),
        ServerPlannedAction(action='service_restart',parameters={'service_name':'netmon.service'},rationale='recover failed service'),
        ServerPlannedAction(action='service_status',parameters={'service_name':'netmon.service'},rationale='verify recovery'),
        ServerPlannedAction(action='finish',parameters={},rationale='service recovered and verified'),
    ]
    async def fake_plan(objective,node,context): return plans.pop(0)
    monkeypatch.setattr('app.ai.router.plan_server_with_router',fake_plan)
    old=settings.ai_provider; object.__setattr__(settings,'ai_provider','router')
    try:
        session=client.post('/api/v1/server/sessions',headers=auth,json={'agent_id':agent['id'],'objective':'netmon mati, cari masalah dan bereskan'}).json()
        task=client.post('/api/v1/agent/tasks/claim',headers=_h(agent)).json()['task']; assert task['capability']=='system.status'
        client.post(f"/api/v1/agent/tasks/{task['id']}/complete",headers=_h(agent),json={'status':'success','result':{'load':{'1m':0.1},'memory':{'available':100}}})
        logs=client.post('/api/v1/agent/tasks/claim',headers=_h(agent)).json()['task']; assert logs['capability']=='service.logs'
        client.post(f"/api/v1/agent/tasks/{logs['id']}/complete",headers=_h(agent),json={'status':'success','result':{'logs':'service failed'}})
        restart=client.post('/api/v1/agent/tasks/claim',headers=_h(agent)).json()['task']; assert restart['capability']=='service.restart'
        client.post(f"/api/v1/agent/tasks/{restart['id']}/complete",headers=_h(agent),json={'status':'success','result':{'ok':True}})
        verify=client.post('/api/v1/agent/tasks/claim',headers=_h(agent)).json()['task']; assert verify['capability']=='service.status'
        done=client.post(f"/api/v1/agent/tasks/{verify['id']}/complete",headers=_h(agent),json={'status':'success','result':{'stdout':'active'}})
        assert done.status_code==200,done.text
        state=client.get(f"/api/v1/server/sessions/{session['id']}",headers=auth).json()
        assert state['status']=='success' and state['phase']=='complete' and state['iteration']==4
        assert len(state['context']['history'])==4
    finally: object.__setattr__(settings,'ai_provider',old)


def test_server_session_blocks_planner_capability_escalation(client,auth,monkeypatch):
    from app.core.config import settings
    _org(client,auth); agent=_agent(client,auth,['system.status'])
    async def fake_plan(objective,node,context): return ServerPlannedAction(action='docker_restart',parameters={'container_name':'db'},rationale='try unavailable action')
    monkeypatch.setattr('app.ai.router.plan_server_with_router',fake_plan)
    old=settings.ai_provider; object.__setattr__(settings,'ai_provider','router')
    try:
        session=client.post('/api/v1/server/sessions',headers=auth,json={'agent_id':agent['id'],'objective':'fix server'}).json()
        task=client.post('/api/v1/agent/tasks/claim',headers=_h(agent)).json()['task']
        client.post(f"/api/v1/agent/tasks/{task['id']}/complete",headers=_h(agent),json={'status':'success','result':{'ok':True}})
        state=client.get(f"/api/v1/server/sessions/{session['id']}",headers=auth).json()
        assert state['status']=='blocked_capability' and 'unavailable' in state['result']['error']
    finally: object.__setattr__(settings,'ai_provider',old)


def test_operator_natural_language_starts_server_diagnosis(client,auth):
    _org(client,auth); agent=_agent(client,auth,['system.status'])
    r=client.post('/api/v1/operator/command',headers=auth,json={'organization_id':'org-server','command':'server ubuntu-main lambat, cari masalah server dan bereskan'})
    assert r.status_code==200,r.text
    body=r.json(); assert body['status']=='success' and body['plan'][0]['tool']=='server.diagnose'
    sessions=client.get('/api/v1/server/sessions?agent_id=ubuntu-main',headers=auth).json()
    assert len(sessions)==1 and sessions[0]['status']=='running'


def test_server_session_requires_server_agent(client,auth):
    _org(client,auth)
    r=client.post('/api/v1/agents',headers=auth,json={'id':'laptop','organization_id':'org-server','name':'laptop','kind':'local','capabilities':['system.status']}); assert r.status_code==200
    bad=client.post('/api/v1/server/sessions',headers=auth,json={'agent_id':'laptop','objective':'fix it'})
    assert bad.status_code==400
