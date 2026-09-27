from pathlib import Path
from datetime import datetime, timezone, timedelta

from app.models import ServerPlannedAction


def _org(client, auth, oid="org-m5"):
    r=client.post('/api/v1/organizations',headers=auth,json={'id':oid,'name':oid,'slug':oid}); assert r.status_code==200,r.text


def _agent(client, auth, caps, aid="ubuntu-m5", org="org-m5"):
    r=client.post('/api/v1/agents',headers=auth,json={'id':aid,'organization_id':org,'name':aid,'kind':'server','capabilities':caps}); assert r.status_code==200,r.text; return r.json()


def test_package_apply_updates_is_approval_by_default(client, auth):
    _org(client,auth)
    agent=_agent(client,auth,['package.updates','package.security_updates','package.apply_updates'])
    read=client.post('/api/v1/infrastructure/actions',headers=auth,json={'agent_id':agent['id'],'capability':'package.security_updates','payload':{},'reason':'inspect security updates'}).json()
    assert read['status']=='queued' and read['policy']=='read'
    apply=client.post('/api/v1/infrastructure/actions',headers=auth,json={'agent_id':agent['id'],'capability':'package.apply_updates','payload':{'packages':['openssl']},'reason':'apply approved security update'}).json()
    assert apply['status']=='approval_required'


def test_server_planner_maps_read_only_diagnostics_and_package_update():
    from app.services.server_ops import _capability_for
    cases={
        'process_list':'system.processes','network_sockets':'network.sockets','network_routes':'network.routes',
        'firewall_status':'firewall.status','package_updates':'package.updates','package_security_updates':'package.security_updates',
    }
    for action,expected in cases.items():
        cap,_=_capability_for(ServerPlannedAction(action=action,parameters={}))
        assert cap==expected
    cap,payload=_capability_for(ServerPlannedAction(action='package_apply_updates',parameters={'packages':['openssl','curl']}))
    assert cap=='package.apply_updates' and payload['packages']==['openssl','curl']


def test_artifact_retention_dry_run_and_delete(client, auth, tmp_path):
    from app.core.config import settings
    from app.services.artifacts import store_artifact, prune_artifacts, get_artifact
    from app.core.db import connect, dumps
    _org(client,auth)
    old=settings.artifact_dir
    object.__setattr__(settings,'artifact_dir',str(tmp_path/'artifacts'))
    try:
        a1=store_artifact(organization_id='org-m5',project_id='p1',kind='release',filename='a.bin',content=b'a')
        a2=store_artifact(organization_id='org-m5',project_id='p1',kind='release',filename='b.bin',content=b'b')
        a3=store_artifact(organization_id='org-m5',project_id='p1',kind='release',filename='c.bin',content=b'c')
        old_time=(datetime.now(timezone.utc)-timedelta(days=60)).isoformat()
        with connect() as conn:
            conn.execute('UPDATE artifacts SET created_at=? WHERE id IN (?,?)',(old_time,a1['id'],a2['id']))
            conn.execute("INSERT INTO project_releases(id,organization_id,project_id,target_id,deployment_run_id,revision,artifact_id,release_path,status,metadata_json,created_at,activated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         ('rel-protected','org-m5','p1','t1','run1','abc',a1['id'],'/tmp/r','active',dumps({}),old_time,old_time))
        preview=prune_artifacts(organization_id='org-m5',retention_days=30,max_per_project=2,dry_run=True)
        assert a1['id'] not in preview['candidate_ids']
        assert a2['id'] in preview['candidate_ids']
        result=prune_artifacts(organization_id='org-m5',retention_days=30,max_per_project=2,dry_run=False)
        assert a2['id'] in result['deleted_ids']
        assert get_artifact(a1['id']) is not None and get_artifact(a2['id']) is None and get_artifact(a3['id']) is not None
    finally:
        object.__setattr__(settings,'artifact_dir',old)


def test_server_release_cleanup_never_deletes_current(tmp_path, monkeypatch):
    import agent.runner as runner
    root=tmp_path/'apps'; root.mkdir()
    target=root/'demo'; releases=target/'releases'; releases.mkdir(parents=True)
    paths=[]
    for i in range(6):
        p=releases/f'r{i}'; p.mkdir(); (p/'x').write_text(str(i)); paths.append(p)
    current=target/'current'; current.symlink_to(paths[0],target_is_directory=True)
    monkeypatch.setattr(runner,'ALLOWED_ROOTS',[root.resolve()])
    result=runner._deploy_cleanup({'target_root':str(target),'keep':2})
    assert result['ok'] is True
    assert paths[0].exists() and current.resolve()==paths[0].resolve()
    assert len(result['deleted'])>=3
    assert all(Path(x).parent==releases.resolve() for x in result['deleted'])


def test_project_and_target_can_be_disabled(client, auth):
    _org(client,auth)
    local=client.post('/api/v1/agents',headers=auth,json={'id':'local-m5','organization_id':'org-m5','name':'Local M5','kind':'local','capabilities':['repo.inspect','repo.prepare_release']}).json()
    server=client.post('/api/v1/agents',headers=auth,json={'id':'server-m5','organization_id':'org-m5','name':'Server M5','kind':'server','capabilities':['deploy.stage','deploy.activate','deploy.health','deploy.rollback']}).json()
    p=client.post('/api/v1/projects',headers=auth,json={'id':'proj-m5','organization_id':'org-m5','name':'Project M5','source_agent_id':local['id'],'repo_path':'/tmp/proj-m5'}); assert p.status_code==200,p.text
    t=client.post('/api/v1/deployment-targets',headers=auth,json={'id':'target-m5','project_id':'proj-m5','organization_id':'org-m5','server_agent_id':server['id'],'environment':'production','target_root':'/srv/proj-m5','service_type':'none'}); assert t.status_code==200,t.text
    d1=client.post('/api/v1/projects/proj-m5/enabled',headers=auth,json={'enabled':False}); assert d1.status_code==200 and d1.json()['enabled'] is False
    assert all(x['id']!='proj-m5' for x in client.get('/api/v1/projects',headers=auth).json())
    d2=client.post('/api/v1/deployment-targets/target-m5/enabled',headers=auth,json={'enabled':False}); assert d2.status_code==200 and d2.json()['enabled'] is False


def test_package_apply_updates_requires_agent_opt_in(monkeypatch):
    import agent.runner as runner
    monkeypatch.setattr(runner,'ALLOW_PACKAGE_UPDATES',False)
    try:
        runner._apply_package_updates({'packages':['openssl']})
        assert False, 'expected package update to be disabled'
    except RuntimeError as exc:
        assert 'disabled' in str(exc)


def test_package_apply_updates_rejects_unlisted_package(monkeypatch):
    import agent.runner as runner
    monkeypatch.setattr(runner,'ALLOW_PACKAGE_UPDATES',True)
    monkeypatch.setattr(runner,'ALLOWED_PACKAGES',('openssl',))
    try:
        runner._apply_package_updates({'packages':['bash']})
        assert False, 'expected allow-list rejection'
    except RuntimeError as exc:
        assert 'outside allow-list' in str(exc)
