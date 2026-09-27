import hashlib
import hmac
import json
import time

from app.core.db import connect
from app.models import ResourceCreate, ResourceEdgeCreate
from app.services.jobs import get_job
from app.services.resources import add_edge, impact, upsert_resource
from app.services.vault import get_secret, put_secret
from app.worker import process_one_job


def register(client, auth, manifest):
    r=client.post('/api/v1/applications',headers=auth,json=manifest); assert r.status_code==200,r.text


def org_manifest(app_id, org_id='global'):
    return {
        'id':app_id,'name':app_id.title(),'type':'generic','organization_id':org_id,
        'connector':{'type':'simulator'},
        'capabilities':[
            {'name':'status.read','default_policy':'read'},
            {'name':'ops.run','default_policy':'auto'},
            {'name':'ops.fail','default_policy':'auto','retry':{'max_attempts':1}},
            {'name':'ops.verify','default_policy':'auto','verification':{'mode':'connector'},'rollback_capability':'ops.rollback'},
            {'name':'ops.rollback','default_policy':'auto'}
        ]
    }


def create_org_identity(client, auth, org='org-a', role='operator'):
    assert client.post('/api/v1/organizations',headers=auth,json={'id':org,'name':org,'slug':org}).status_code==200
    pid=f'{org}-user'
    assert client.post('/api/v1/principals',headers=auth,json={'id':pid,'name':pid,'kind':'human'}).status_code==200
    key=client.post('/api/v1/api-keys',headers=auth,json={'principal_id':pid,'organization_id':org,'role':role,'label':'test'}).json()
    return key


def test_rbac_org_scoping_and_operator_action(client, auth):
    key=create_org_identity(client,auth,'org-a','operator')
    client.post('/api/v1/organizations',headers=auth,json={'id':'org-b','name':'org-b','slug':'org-b'})
    register(client,auth,org_manifest('app-a','org-a')); register(client,auth,org_manifest('app-b','org-b'))
    bearer={'Authorization':f"Bearer {key['token']}"}
    apps=client.get('/api/v1/applications',headers=bearer).json()
    assert [a['id'] for a in apps]==['app-a']
    ok=client.post('/api/v1/actions/execute',headers=bearer,json={'app_id':'app-a','capability':'ops.run'}); assert ok.json()['status']=='success'
    denied=client.post('/api/v1/actions/execute',headers=bearer,json={'app_id':'app-b','capability':'ops.run'}); assert denied.status_code==403


def test_viewer_cannot_execute(client, auth):
    key=create_org_identity(client,auth,'org-v','viewer'); register(client,auth,org_manifest('app-v','org-v'))
    bearer={'Authorization':f"Bearer {key['token']}"}
    assert client.get('/api/v1/applications',headers=bearer).status_code==200
    assert client.post('/api/v1/actions/execute',headers=bearer,json={'app_id':'app-v','capability':'ops.run'}).status_code==403


def test_revoked_api_key_stops_working(client, auth):
    key=create_org_identity(client,auth,'org-r','viewer'); bearer={'Authorization':f"Bearer {key['token']}"}
    assert client.get('/api/v1/applications',headers=bearer).status_code==200
    assert client.delete(f"/api/v1/api-keys/{key['id']}",headers=auth).status_code==200
    assert client.get('/api/v1/applications',headers=bearer).status_code==401


def test_encrypted_vault_does_not_store_plaintext(client):
    meta=put_secret('connector:x','token','super-secret-value')
    assert get_secret('connector:x','token')=='super-secret-value'
    with connect() as conn:
        row=conn.execute('SELECT ciphertext FROM vault_secrets WHERE id=?',(meta['id'],)).fetchone()
    assert 'super-secret-value' not in row['ciphertext']


def test_signed_webhook_and_replay_protection(client, auth):
    register(client,auth,org_manifest('hook-app'))
    rotated=client.post('/api/v1/applications/hook-app/webhook-secret/rotate',headers=auth).json(); secret=rotated['secret']
    body=json.dumps({'event_type':'system.alert','payload':{'severity':'high','message':'disk high'}},separators=(',',':')).encode()
    ts=str(int(time.time())); sig=hmac.new(secret.encode(),ts.encode()+b'.'+body,hashlib.sha256).hexdigest()
    headers={'X-AIOC-Timestamp':ts,'X-AIOC-Signature':f'sha256={sig}','Content-Type':'application/json'}
    r=client.post('/api/v1/events/webhook/hook-app',headers=headers,content=body); assert r.status_code==200,r.text
    replay=client.post('/api/v1/events/webhook/hook-app',headers=headers,content=body); assert replay.status_code==401
    incidents=client.get('/api/v1/incidents',headers=auth).json(); assert incidents and incidents[0]['app_id']=='hook-app'


def test_signed_webhook_rejects_bad_signature(client, auth):
    register(client,auth,org_manifest('hook-bad')); client.post('/api/v1/applications/hook-bad/webhook-secret/rotate',headers=auth)
    body=b'{"event_type":"system.alert","payload":{}}'; ts=str(int(time.time()))
    r=client.post('/api/v1/events/webhook/hook-bad',headers={'X-AIOC-Timestamp':ts,'X-AIOC-Signature':'bad','Content-Type':'application/json'},content=body)
    assert r.status_code==401


def test_durable_queue_action_processes(client, auth):
    register(client,auth,org_manifest('queue-app'))
    q=client.post('/api/v1/actions/queue',headers=auth,json={'request':{'app_id':'queue-app','capability':'ops.run','parameters':{}},'max_attempts':2}).json()
    assert q['status']=='queued'; jid=q['job']['id']; assert get_job(jid)['status']=='queued'
    import asyncio; assert asyncio.run(process_one_job()) is True
    assert get_job(jid)['status']=='success'


def test_rule_can_enqueue_durable_action(client, auth):
    register(client,auth,org_manifest('rule-q'))
    rule={'name':'durable','trigger_type':'event','trigger':{'event_type':'xx','source_app_id':'rule-q'},'action':{'app_id':'rule-q','capability':'ops.run','delivery':'durable','parameters':{}}}
    assert client.post('/api/v1/rules',headers=auth,json=rule).status_code==200
    result=client.post('/api/v1/events',headers={'X-Event-Token':'test-event'},json={'source_app_id':'rule-q','event_type':'xx','payload':{}}).json()
    assert result['fired'][0]['result']['status']=='queued'


def test_circuit_breaker_opens_and_incident_deduplicates(client, auth):
    register(client,auth,org_manifest('cb-app'))
    payload={'app_id':'cb-app','capability':'ops.fail','parameters':{'simulate_failure':True}}
    assert client.post('/api/v1/actions/execute',headers=auth,json=payload).json()['status']=='failed'
    assert client.post('/api/v1/actions/execute',headers=auth,json=payload).json()['status']=='failed'
    third=client.post('/api/v1/actions/execute',headers=auth,json=payload).json(); assert third['status']=='blocked'; assert 'circuit breaker' in third['message']
    incidents=client.get('/api/v1/incidents?status=open',headers=auth).json(); assert len([i for i in incidents if i['app_id']=='cb-app'])==1


def test_verification_failure_triggers_rollback_and_incident(client, auth):
    register(client,auth,org_manifest('verify-app'))
    result=client.post('/api/v1/actions/execute',headers=auth,json={'app_id':'verify-app','capability':'ops.verify','parameters':{'simulate_verification_failure':True}}).json()
    assert result['status']=='failed'; assert result['data']['rollback']['ok'] is True; assert result['data']['incident_id']


def test_connector_lifecycle_validate(client, auth):
    register(client,auth,org_manifest('life-app'))
    before=client.get('/api/v1/applications/life-app/lifecycle',headers=auth).json(); assert before['lifecycle_status']=='registered'
    validated=client.post('/api/v1/applications/life-app/validate',headers=auth).json(); assert validated['status']=='enabled'
    after=client.get('/api/v1/applications/life-app/lifecycle',headers=auth).json(); assert after['last_health']['ok'] is True


def test_resource_graph_impact(client):
    a=upsert_resource(ResourceCreate(resource_type='router',external_id='r1',name='Router'))
    b=upsert_resource(ResourceCreate(resource_type='application',external_id='a1',name='App'))
    c=upsert_resource(ResourceCreate(resource_type='database',external_id='d1',name='DB'))
    add_edge(ResourceEdgeCreate(from_resource_id=a['id'],to_resource_id=b['id'],relation='connects'))
    add_edge(ResourceEdgeCreate(from_resource_id=b['id'],to_resource_id=c['id'],relation='depends_on'))
    graph=impact(a['id']); assert len(graph['resources'])==3; assert len(graph['edges'])>=2


def test_resource_api(client, auth):
    first=client.post('/api/v1/resources',headers=auth,json={'resource_type':'server','external_id':'s1','name':'Server 1','status':'healthy'}).json()
    rows=client.get('/api/v1/resources',headers=auth).json(); assert rows[0]['id']==first['id']
    impact_result=client.get(f"/api/v1/resources/{first['id']}/impact",headers=auth); assert impact_result.status_code==200


def test_workflow_parallelism_policy_executes_independent_steps(client, auth):
    for name in ('pa','pb'):
        assert client.post('/api/v1/applications',headers=auth,json={
            'id':f'app-{name}','name':f'App {name}','type':'sim','connector':{'type':'simulator'},
            'capabilities':[{'name':'op.run','default_policy':'auto'}]}).status_code==200
    wf=client.post('/api/v1/workflows',headers=auth,json={
        'organization_id':'global','name':'parallel','max_parallel_steps':2,
        'steps':[{'id':'a','app_id':'app-pa','capability':'op.run'},{'id':'b','app_id':'app-pb','capability':'op.run'}]}).json()
    queued=client.post(f"/api/v1/workflows/{wf['id']}/run",headers=auth,json={'reason':'parallel-test'}).json()
    from app.worker import process_one_job
    import asyncio
    assert asyncio.run(process_one_job()) is True
    run=client.get(f"/api/v1/workflow-runs/{queued['run']['id']}",headers=auth).json()
    assert run['status']=='success'
    assert run['result']['max_parallel_steps']==2
    assert set(run['result']['step_statuses'])=={'a','b'}
