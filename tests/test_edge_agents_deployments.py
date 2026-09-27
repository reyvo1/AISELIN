import hashlib


def _org(client, auth, oid="org-edge"):
    r=client.post('/api/v1/organizations',headers=auth,json={'id':oid,'name':oid,'slug':oid})
    assert r.status_code==200,r.text


def _agent(client,auth,aid,kind,caps,org='org-edge'):
    r=client.post('/api/v1/agents',headers=auth,json={'id':aid,'organization_id':org,'name':aid,'kind':kind,'capabilities':caps})
    assert r.status_code==200,r.text
    body=r.json(); assert body['token'].startswith('aioc_agent_'); return body


def _agent_headers(agent): return {'Authorization':'Bearer '+agent['token']}


def _project_and_target(client,auth,local,server,org='org-edge',target_id='prod'):
    p=client.post('/api/v1/projects',headers=auth,json={
        'id':'proj','organization_id':org,'name':'Project','source_agent_id':local['id'],'repo_path':'/workspace/project',
        'build_steps':[['python','-m','compileall','.']],'test_steps':[['pytest','-q']]
    })
    assert p.status_code==200,p.text
    t=client.post('/api/v1/deployment-targets',headers=auth,json={
        'id':target_id,'project_id':'proj','organization_id':org,'server_agent_id':server['id'],'target_root':'/srv/apps/proj',
        'service_type':'systemd','service_name':'proj.service','health_url':'http://127.0.0.1:8080/health'
    })
    assert t.status_code==200,t.text
    return t.json()


def test_agent_heartbeat_task_capability_and_lease(client,auth):
    _org(client,auth)
    agent=_agent(client,auth,'local-1','local',['system.status','repo.inspect','repo.prepare_release'])
    h=client.post('/api/v1/agent/heartbeat',headers=_agent_headers(agent),json={'version':'1.0','capabilities':['system.status','repo.inspect'],'status':'online'})
    assert h.status_code==200,h.text
    assert h.json()['status']=='online'
    bad=client.post('/api/v1/agent-tasks',headers=auth,json={'agent_id':'local-1','capability':'shell.anything','payload':{}})
    assert bad.status_code==400
    task=client.post('/api/v1/agent-tasks',headers=auth,json={'agent_id':'local-1','capability':'system.status','payload':{}})
    assert task.status_code==200,task.text
    claimed=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(agent)).json()['task']
    assert claimed['id']==task.json()['id'] and claimed['status']=='running' and claimed['attempts']==1
    assert client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(agent)).json()['task'] is None
    done=client.post(f"/api/v1/agent/tasks/{claimed['id']}/complete",headers=_agent_headers(agent),json={'status':'success','result':{'ok':True}})
    assert done.status_code==200 and done.json()['task']['status']=='success'


def test_agent_rejects_unregistered_capability_in_heartbeat(client,auth):
    _org(client,auth)
    agent=_agent(client,auth,'local-2','local',['system.status'])
    r=client.post('/api/v1/agent/heartbeat',headers=_agent_headers(agent),json={'capabilities':['system.status','root.shell']})
    assert r.status_code==400 and 'undeclared' in r.text


def test_artifact_checksum_and_tenant_scope(client,auth):
    _org(client,auth)
    local=_agent(client,auth,'local-3','local',['repo.inspect','repo.prepare_release'])
    task=client.post('/api/v1/agent-tasks',headers=auth,json={'agent_id':'local-3','capability':'repo.prepare_release','payload':{'project_id':'p'}}).json()
    client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(local))
    content=b'release-bytes'; digest=hashlib.sha256(content).hexdigest()
    bad=client.post(f"/api/v1/agent/tasks/{task['id']}/artifact",headers={**_agent_headers(local),'X-Artifact-SHA256':'0'*64,'X-Project-ID':'p'},content=content)
    assert bad.status_code==400
    good=client.post(f"/api/v1/agent/tasks/{task['id']}/artifact",headers={**_agent_headers(local),'X-Artifact-SHA256':digest,'X-Artifact-Filename':'app.tar.gz','X-Project-ID':'p'},content=content)
    assert good.status_code==200,good.text
    downloaded=client.get(f"/api/v1/agent/artifacts/{good.json()['id']}",headers=_agent_headers(local))
    assert downloaded.status_code==200 and downloaded.content==content and downloaded.headers['x-artifact-sha256']==digest


def test_deployment_state_machine_success(client,auth):
    _org(client,auth)
    local=_agent(client,auth,'dev','local',['repo.inspect','repo.prepare_release'])
    server=_agent(client,auth,'server','server',['deploy.stage','deploy.activate','deploy.health','deploy.rollback'])
    _project_and_target(client,auth,local,server)
    started=client.post('/api/v1/deployment-targets/prod/deploy',headers=auth,json={'reason':'ship it'})
    assert started.status_code==200,started.text
    run_id=started.json()['id']

    inspect=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(local)).json()['task']
    assert inspect['capability']=='repo.inspect'
    r=client.post(f"/api/v1/agent/tasks/{inspect['id']}/complete",headers=_agent_headers(local),json={'status':'success','result':{'ok':True,'revision':'abc123','dirty':False}})
    assert r.status_code==200,r.text

    prepare=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(local)).json()['task']
    assert prepare['capability']=='repo.prepare_release'
    content=b'fake-tar-for-central-state-test'; digest=hashlib.sha256(content).hexdigest()
    artifact=client.post(f"/api/v1/agent/tasks/{prepare['id']}/artifact",headers={**_agent_headers(local),'X-Artifact-SHA256':digest,'X-Project-ID':'proj'},content=content)
    assert artifact.status_code==200,artifact.text
    r=client.post(f"/api/v1/agent/tasks/{prepare['id']}/complete",headers=_agent_headers(local),json={'status':'success','result':{'ok':True,'sha256':digest}})
    assert r.status_code==200,r.text

    stage=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']; assert stage['capability']=='deploy.stage'
    r=client.post(f"/api/v1/agent/tasks/{stage['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'ok':True,'release_path':'/srv/apps/proj/releases/r1'}})
    assert r.status_code==200,r.text
    activate=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']; assert activate['capability']=='deploy.activate'
    client.post(f"/api/v1/agent/tasks/{activate['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'ok':True,'current':'/srv/apps/proj/current'}})
    health=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']; assert health['capability']=='deploy.health'
    final=client.post(f"/api/v1/agent/tasks/{health['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'ok':True,'status_code':200}})
    assert final.status_code==200,final.text
    run=client.get(f'/api/v1/deployments/{run_id}',headers=auth).json()
    assert run['status']=='success' and run['phase']=='complete' and run['source_revision']=='abc123'
    releases=client.get('/api/v1/deployment-targets/prod/releases',headers=auth).json()
    assert releases[0]['status']=='active' and releases[0]['artifact_id']==artifact.json()['id']


def test_dirty_source_blocks_before_packaging(client,auth):
    _org(client,auth)
    local=_agent(client,auth,'dev','local',['repo.inspect','repo.prepare_release'])
    server=_agent(client,auth,'server','server',['deploy.stage','deploy.activate','deploy.health','deploy.rollback'])
    _project_and_target(client,auth,local,server)
    run=client.post('/api/v1/deployment-targets/prod/deploy',headers=auth,json={'allow_dirty':False}).json()
    inspect=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(local)).json()['task']
    client.post(f"/api/v1/agent/tasks/{inspect['id']}/complete",headers=_agent_headers(local),json={'status':'success','result':{'revision':'dirty1','dirty':True}})
    state=client.get(f"/api/v1/deployments/{run['id']}",headers=auth).json()
    assert state['status']=='failed' and 'dirty' in state['result']['failure']['error']
    assert client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(local)).json()['task'] is None


def _drive_release_success(client,auth,local,server,revision,release_path):
    started=client.post('/api/v1/deployment-targets/prod/deploy',headers=auth,json={'reason':f'deploy {revision}'})
    assert started.status_code==200,started.text
    run=started.json()
    inspect=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(local)).json()['task']
    client.post(f"/api/v1/agent/tasks/{inspect['id']}/complete",headers=_agent_headers(local),json={'status':'success','result':{'revision':revision,'dirty':False}})
    prepare=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(local)).json()['task']
    content=(f'artifact-{revision}').encode(); digest=hashlib.sha256(content).hexdigest()
    artifact=client.post(f"/api/v1/agent/tasks/{prepare['id']}/artifact",headers={**_agent_headers(local),'X-Artifact-SHA256':digest,'X-Project-ID':'proj'},content=content)
    assert artifact.status_code==200,artifact.text
    client.post(f"/api/v1/agent/tasks/{prepare['id']}/complete",headers=_agent_headers(local),json={'status':'success','result':{'ok':True,'sha256':digest}})
    stage=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']
    client.post(f"/api/v1/agent/tasks/{stage['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'release_path':release_path}})
    activate=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']
    client.post(f"/api/v1/agent/tasks/{activate['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'ok':True}})
    health=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']
    client.post(f"/api/v1/agent/tasks/{health['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'ok':True,'status_code':200}})
    return client.get(f"/api/v1/deployments/{run['id']}",headers=auth).json()


def test_failed_health_rolls_back_to_previous_active_release(client,auth):
    _org(client,auth)
    local=_agent(client,auth,'dev','local',['repo.inspect','repo.prepare_release'])
    server=_agent(client,auth,'server','server',['deploy.stage','deploy.activate','deploy.health','deploy.rollback'])
    _project_and_target(client,auth,local,server)
    first=_drive_release_success(client,auth,local,server,'rev-one','/srv/apps/proj/releases/r1')
    assert first['status']=='success'

    started=client.post('/api/v1/deployment-targets/prod/deploy',headers=auth,json={'reason':'bad release'}).json()
    inspect=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(local)).json()['task']
    client.post(f"/api/v1/agent/tasks/{inspect['id']}/complete",headers=_agent_headers(local),json={'status':'success','result':{'revision':'rev-two','dirty':False}})
    prepare=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(local)).json()['task']
    content=b'artifact-two'; digest=hashlib.sha256(content).hexdigest()
    client.post(f"/api/v1/agent/tasks/{prepare['id']}/artifact",headers={**_agent_headers(local),'X-Artifact-SHA256':digest,'X-Project-ID':'proj'},content=content)
    client.post(f"/api/v1/agent/tasks/{prepare['id']}/complete",headers=_agent_headers(local),json={'status':'success','result':{'ok':True}})
    stage=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']
    client.post(f"/api/v1/agent/tasks/{stage['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'release_path':'/srv/apps/proj/releases/r2'}})
    activate=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']
    client.post(f"/api/v1/agent/tasks/{activate['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'ok':True}})
    health=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']
    response=client.post(f"/api/v1/agent/tasks/{health['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'ok':False,'status_code':500}})
    assert response.status_code==200,response.text
    rollback=client.post('/api/v1/agent/tasks/claim',headers=_agent_headers(server)).json()['task']
    assert rollback['capability']=='deploy.rollback'
    assert rollback['payload']['previous_release_path']=='/srv/apps/proj/releases/r1'
    client.post(f"/api/v1/agent/tasks/{rollback['id']}/complete",headers=_agent_headers(server),json={'status':'success','result':{'ok':True,'current':'/srv/apps/proj/releases/r1'}})
    state=client.get(f"/api/v1/deployments/{started['id']}",headers=auth).json()
    assert state['status']=='rolled_back' and state['phase']=='rolled_back'
    releases=client.get('/api/v1/deployment-targets/prod/releases',headers=auth).json()
    active=[r for r in releases if r['status']=='active']; assert len(active)==1 and active[0]['revision']=='rev-one'


def test_concurrent_deployments_same_project_target_are_blocked(client,auth):
    _org(client,auth)
    local=_agent(client,auth,'dev','local',['repo.inspect','repo.prepare_release'])
    server=_agent(client,auth,'server','server',['deploy.stage','deploy.activate','deploy.health','deploy.rollback'])
    _project_and_target(client,auth,local,server)
    first=client.post('/api/v1/deployment-targets/prod/deploy',headers=auth,json={'reason':'first'})
    assert first.status_code==200,first.text
    second=client.post('/api/v1/deployment-targets/prod/deploy',headers=auth,json={'reason':'second'})
    assert second.status_code==400 and 'busy' in second.text
