from app.models import DeveloperPlannedAction


def _org(client,auth,oid='org-dev'):
    r=client.post('/api/v1/organizations',headers=auth,json={'id':oid,'name':oid,'slug':oid}); assert r.status_code==200,r.text


def _agent(client,auth,aid,kind,caps,org='org-dev'):
    r=client.post('/api/v1/agents',headers=auth,json={'id':aid,'organization_id':org,'name':aid,'kind':kind,'capabilities':caps}); assert r.status_code==200,r.text; return r.json()


def _h(agent): return {'Authorization':'Bearer '+agent['token']}


def _project(client,auth,source,org='org-dev'):
    r=client.post('/api/v1/projects',headers=auth,json={'id':'finance','organization_id':org,'name':'Finance App','source_agent_id':source['id'],'repo_path':'/home/user/finance','test_steps':[['pytest','-q']]}); assert r.status_code==200,r.text; return r.json()


def test_developer_requires_scoped_capabilities(client,auth):
    _org(client,auth)
    source=_agent(client,auth,'dev','local',['repo.inspect','repo.prepare_release'])
    _project(client,auth,source)
    r=client.post('/api/v1/developer/sessions',headers=auth,json={'project_id':'finance','objective':'fix the bug'})
    assert r.status_code==400 and 'missing developer capabilities' in r.text


def test_developer_blocks_dirty_baseline_before_ai(client,auth):
    _org(client,auth)
    caps=['repo.inspect','repo.prepare_release','repo.context','repo.read','repo.search','repo.apply_patch','repo.run_steps']
    source=_agent(client,auth,'dev','local',caps); _project(client,auth,source)
    session=client.post('/api/v1/developer/sessions',headers=auth,json={'project_id':'finance','objective':'fix rounding bug'}).json()
    task=client.post('/api/v1/agent/tasks/claim',headers=_h(source)).json()['task']; assert task['capability']=='repo.context'
    done=client.post(f"/api/v1/agent/tasks/{task['id']}/complete",headers=_h(source),json={'status':'success','result':{'revision':'abc','dirty':True,'tree':['app.py']}})
    assert done.status_code==200,done.text
    state=client.get(f"/api/v1/developer/sessions/{session['id']}",headers=auth).json()
    assert state['status']=='blocked_dirty' and state['baseline_revision']=='abc'


def test_developer_patch_forces_post_patch_test(client,auth,monkeypatch):
    from app.core.config import settings
    _org(client,auth)
    caps=['repo.inspect','repo.prepare_release','repo.context','repo.read','repo.search','repo.apply_patch','repo.run_steps']
    source=_agent(client,auth,'dev','local',caps); _project(client,auth,source)
    calls=[]
    async def fake_plan(objective,project,context):
        calls.append(context)
        if len(calls)==1:
            return DeveloperPlannedAction(action='patch',parameters={'patch':'--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n'},rationale='root cause')
        return DeveloperPlannedAction(action='finish',parameters={},rationale='fixed')
    monkeypatch.setattr('app.ai.router.plan_developer_with_router',fake_plan)
    old=settings.ai_provider; object.__setattr__(settings,'ai_provider','router')
    try:
        session=client.post('/api/v1/developer/sessions',headers=auth,json={'project_id':'finance','objective':'fix bug'}).json()
        context_task=client.post('/api/v1/agent/tasks/claim',headers=_h(source)).json()['task']
        client.post(f"/api/v1/agent/tasks/{context_task['id']}/complete",headers=_h(source),json={'status':'success','result':{'revision':'abc','dirty':False,'tree':['app.py']}})
        patch=client.post('/api/v1/agent/tasks/claim',headers=_h(source)).json()['task']; assert patch['capability']=='repo.apply_patch'
        client.post(f"/api/v1/agent/tasks/{patch['id']}/complete",headers=_h(source),json={'status':'success','result':{'ok':True,'diffstat':'app.py | 2'}})
        mandatory=client.post('/api/v1/agent/tasks/claim',headers=_h(source)).json()['task']
        assert mandatory['capability']=='repo.run_steps'
        finish=client.post(f"/api/v1/agent/tasks/{mandatory['id']}/complete",headers=_h(source),json={'status':'success','result':{'ok':True,'count':1}})
        assert finish.status_code==200,finish.text
        state=client.get(f"/api/v1/developer/sessions/{session['id']}",headers=auth).json()
        assert state['status']=='success' and state['context']['patch_count']==1 and state['context']['verified_after_patch'] is True
    finally: object.__setattr__(settings,'ai_provider',old)


def test_operator_language_deploys_named_project(client,auth):
    _org(client,auth)
    local=_agent(client,auth,'laptop','local',['repo.inspect','repo.prepare_release'])
    server=_agent(client,auth,'ubuntu','server',['deploy.stage','deploy.activate','deploy.health','deploy.rollback'])
    _project(client,auth,local)
    t=client.post('/api/v1/deployment-targets',headers=auth,json={'id':'finance-prod','project_id':'finance','organization_id':'org-dev','server_agent_id':'ubuntu','environment':'production','target_root':'/srv/finance','service_type':'none'})
    assert t.status_code==200,t.text
    r=client.post('/api/v1/operator/command',headers=auth,json={'organization_id':'org-dev','command':'tolong sinkronkan Finance App ke server produksi'})
    assert r.status_code==200,r.text
    body=r.json(); assert body['status']=='success' and body['plan'][0]['tool']=='project.deploy'
    deployments=client.get('/api/v1/deployments?project_id=finance',headers=auth).json(); assert len(deployments)==1 and deployments[0]['phase']=='source_inspect'


def test_operator_server_status_creates_agent_task(client,auth):
    _org(client,auth)
    server=_agent(client,auth,'ubuntu-main','server',['system.status'])
    r=client.post('/api/v1/operator/command',headers=auth,json={'organization_id':'org-dev','command':'cek kondisi server ubuntu-main'})
    assert r.status_code==200,r.text
    task=client.post('/api/v1/agent/tasks/claim',headers=_h(server)).json()['task']
    assert task['capability']=='system.status'


def test_operator_language_starts_autonomous_developer(client,auth):
    _org(client,auth)
    caps=['repo.inspect','repo.prepare_release','repo.context','repo.read','repo.search','repo.apply_patch','repo.run_steps']
    source=_agent(client,auth,'dev','local',caps); _project(client,auth,source)
    r=client.post('/api/v1/operator/command',headers=auth,json={'organization_id':'org-dev','command':'perbaiki bug di Finance App'})
    assert r.status_code==200,r.text
    body=r.json(); assert body['status']=='success' and body['plan'][0]['tool']=='project.develop'
    sessions=client.get('/api/v1/developer/sessions?project_id=finance',headers=auth).json()
    assert len(sessions)==1 and sessions[0]['status']=='running'


def test_developer_blocks_auto_deploy_when_repo_dirty_after_commit(client,auth,monkeypatch):
    from app.core.config import settings
    _org(client,auth)
    caps=['repo.inspect','repo.prepare_release','repo.context','repo.read','repo.search','repo.apply_patch','repo.run_steps','repo.commit']
    source=_agent(client,auth,'dev','local',caps)
    server=_agent(client,auth,'srv','server',['deploy.stage','deploy.activate','deploy.health','deploy.rollback'])
    _project(client,auth,source)
    t=client.post('/api/v1/deployment-targets',headers=auth,json={'id':'finance-prod','project_id':'finance','organization_id':'org-dev','server_agent_id':'srv','environment':'production','target_root':'/srv/finance','service_type':'none'})
    assert t.status_code==200,t.text
    async def fake_plan(objective,project,context):
        return DeveloperPlannedAction(action='finish',parameters={},rationale='no source patch needed')
    monkeypatch.setattr('app.ai.router.plan_developer_with_router',fake_plan)
    old=settings.ai_provider; object.__setattr__(settings,'ai_provider','router')
    try:
        session=client.post('/api/v1/developer/sessions',headers=auth,json={'project_id':'finance','objective':'verify and deploy','auto_commit':True,'auto_deploy_target_id':'finance-prod'}).json()
        context_task=client.post('/api/v1/agent/tasks/claim',headers=_h(source)).json()['task']
        client.post(f"/api/v1/agent/tasks/{context_task['id']}/complete",headers=_h(source),json={'status':'success','result':{'revision':'abc','dirty':False,'tree':['app.py']}})
        commit=client.post('/api/v1/agent/tasks/claim',headers=_h(source)).json()['task']; assert commit['capability']=='repo.commit'
        done=client.post(f"/api/v1/agent/tasks/{commit['id']}/complete",headers=_h(source),json={'status':'success','result':{'revision':'def','committed':False,'dirty':True,'remaining_changes':['?? __pycache__/']}})
        assert done.status_code==200,done.text
        state=client.get(f"/api/v1/developer/sessions/{session['id']}",headers=auth).json()
        assert state['status']=='blocked_dirty_after_commit'
        assert 'remaining_changes' in state['result']
        assert client.get('/api/v1/deployments?project_id=finance',headers=auth).json()==[]
    finally: object.__setattr__(settings,'ai_provider',old)


def test_developer_project_lock_blocks_deploy_race(client,auth):
    _org(client,auth)
    caps=['repo.inspect','repo.prepare_release','repo.context','repo.read','repo.search','repo.apply_patch','repo.run_steps']
    source=_agent(client,auth,'dev','local',caps)
    server=_agent(client,auth,'srv','server',['deploy.stage','deploy.activate','deploy.health','deploy.rollback'])
    _project(client,auth,source)
    t=client.post('/api/v1/deployment-targets',headers=auth,json={'id':'finance-prod','project_id':'finance','organization_id':'org-dev','server_agent_id':'srv','environment':'production','target_root':'/srv/finance','service_type':'none'})
    assert t.status_code==200,t.text
    dev=client.post('/api/v1/developer/sessions',headers=auth,json={'project_id':'finance','objective':'inspect and fix'}); assert dev.status_code==200,dev.text
    deploy=client.post('/api/v1/deployment-targets/finance-prod/deploy',headers=auth,json={'reason':'race'})
    assert deploy.status_code==400 and 'busy' in deploy.text
