import hashlib


def _org(client,auth,oid='org-secret'):
    r=client.post('/api/v1/organizations',headers=auth,json={'id':oid,'name':oid,'slug':oid}); assert r.status_code==200,r.text


def _agent(client,auth,aid,kind,caps,metadata=None,org='org-secret'):
    r=client.post('/api/v1/agents',headers=auth,json={'id':aid,'organization_id':org,'name':aid,'kind':kind,'capabilities':caps,'metadata':metadata or {}}); assert r.status_code==200,r.text; return r.json()


def _h(agent): return {'Authorization':'Bearer '+agent['token']}


def _setup(client,auth):
    _org(client,auth)
    local=_agent(client,auth,'dev-secret','local',['repo.inspect','repo.prepare_release'])
    server=_agent(client,auth,'server-secret','server',['deploy.stage','deploy.configure','deploy.activate','deploy.health','deploy.rollback'],{'allowed_secrets':['db-password']})
    p=client.post('/api/v1/projects',headers=auth,json={'id':'proj-secret','organization_id':'org-secret','name':'Secret Project','source_agent_id':local['id'],'repo_path':'/workspace/secret'}); assert p.status_code==200,p.text
    return local,server


def test_agent_secret_grant_survives_heartbeat_and_secret_is_scoped(client,auth):
    _org(client,auth)
    server=_agent(client,auth,'server-secret','server',['system.status'],{'allowed_secrets':['db-password'],'owner_note':'keep'})
    hb=client.post('/api/v1/agent/heartbeat',headers=_h(server),json={'version':'x','capabilities':['system.status'],'metadata':{'hostname':'server-1'}})
    assert hb.status_code==200,hb.text
    assert hb.json()['metadata']['allowed_secrets']==['db-password']
    assert hb.json()['metadata']['runtime']['hostname']=='server-1'
    put=client.post('/api/v1/organizations/org-secret/deployment-secrets/db-password',headers=auth,json={'value':'very-secret'})
    assert put.status_code==200,put.text and 'very-secret' not in put.text
    got=client.get('/api/v1/agent/secrets/db-password',headers=_h(server))
    assert got.status_code==200 and got.json()['value']=='very-secret'
    denied=client.get('/api/v1/agent/secrets/other-secret',headers=_h(server))
    assert denied.status_code==403


def test_target_requires_configure_capability_and_explicit_secret_grant(client,auth):
    local,server=_setup(client,auth)
    ok=client.post('/api/v1/deployment-targets',headers=auth,json={
        'id':'prod-secret','project_id':'proj-secret','organization_id':'org-secret','server_agent_id':server['id'],'target_root':'/srv/apps/secret',
        'config':{'environment':{'APP_ENV':'production'},'secret_env':{'DB_PASSWORD':'db-password'}}
    })
    assert ok.status_code==200,ok.text
    ungranted=_agent(client,auth,'server-no-secret','server',['deploy.stage','deploy.configure','deploy.activate','deploy.health','deploy.rollback'])
    bad=client.post('/api/v1/deployment-targets',headers=auth,json={
        'id':'bad-secret','project_id':'proj-secret','organization_id':'org-secret','server_agent_id':ungranted['id'],'target_root':'/srv/apps/bad',
        'config':{'secret_env':{'DB_PASSWORD':'db-password'}}
    })
    assert bad.status_code==400 and 'not granted' in bad.text


def test_deployment_configuration_uses_secret_reference_not_plaintext(client,auth):
    local,server=_setup(client,auth)
    client.post('/api/v1/organizations/org-secret/deployment-secrets/db-password',headers=auth,json={'value':'VERY-SECRET-PASSWORD'})
    t=client.post('/api/v1/deployment-targets',headers=auth,json={
        'id':'prod-secret','project_id':'proj-secret','organization_id':'org-secret','server_agent_id':server['id'],'target_root':'/srv/apps/secret',
        'config':{'environment':{'APP_ENV':'production'},'secret_env':{'DB_PASSWORD':'db-password'},'env_relative_path':'shared/.env'}
    }); assert t.status_code==200,t.text
    run=client.post('/api/v1/deployment-targets/prod-secret/deploy',headers=auth,json={'reason':'secure deploy'}).json()
    inspect=client.post('/api/v1/agent/tasks/claim',headers=_h(local)).json()['task']
    client.post(f"/api/v1/agent/tasks/{inspect['id']}/complete",headers=_h(local),json={'status':'success','result':{'revision':'secure1','dirty':False}})
    prepare=client.post('/api/v1/agent/tasks/claim',headers=_h(local)).json()['task']
    content=b'archive'; digest=hashlib.sha256(content).hexdigest()
    client.post(f"/api/v1/agent/tasks/{prepare['id']}/artifact",headers={**_h(local),'X-Artifact-SHA256':digest,'X-Project-ID':'proj-secret'},content=content)
    client.post(f"/api/v1/agent/tasks/{prepare['id']}/complete",headers=_h(local),json={'status':'success','result':{'ok':True}})
    stage=client.post('/api/v1/agent/tasks/claim',headers=_h(server)).json()['task']; assert stage['capability']=='deploy.stage'
    client.post(f"/api/v1/agent/tasks/{stage['id']}/complete",headers=_h(server),json={'status':'success','result':{'release_path':'/srv/apps/secret/releases/r1'}})
    config=client.post('/api/v1/agent/tasks/claim',headers=_h(server)).json()['task']
    assert config['capability']=='deploy.configure'
    assert config['payload']['secret_env']=={'DB_PASSWORD':'db-password'}
    assert 'VERY-SECRET-PASSWORD' not in str(config)
    client.post(f"/api/v1/agent/tasks/{config['id']}/complete",headers=_h(server),json={'status':'success','result':{'ok':True,'env_file':'/srv/apps/secret/shared/.env','keys':['APP_ENV','DB_PASSWORD'],'secret_refs':['db-password']}})
    activate=client.post('/api/v1/agent/tasks/claim',headers=_h(server)).json()['task']
    assert activate['capability']=='deploy.activate' and activate['payload']['env_file']=='/srv/apps/secret/shared/.env'
    assert 'VERY-SECRET-PASSWORD' not in str(activate)
    state=client.get(f"/api/v1/deployments/{run['id']}",headers=auth).json()
    assert state['phase']=='deploy_activate'
