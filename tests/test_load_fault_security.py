from __future__ import annotations

import hashlib
import hmac
import json
import time

from app.models import ResourceCreate
from app.services.incidents import open_incident
from app.services.jobs import claim, enqueue
from app.services.locks import acquire, release
from app.services.resources import upsert_resource


def manifest(i, org='global'):
    return {'id':f'app-{i}','name':f'App {i}','type':'generic','organization_id':org,'connector':{'type':'simulator'},'capabilities':[{'name':'read.status','default_policy':'read'},{'name':'op.run','default_policy':'auto'}]}


def test_many_dynamic_apps_health(client, auth):
    for i in range(40): assert client.post('/api/v1/applications',headers=auth,json=manifest(i)).status_code==200
    g=client.get('/api/v1/global/health',headers=auth).json(); assert g['total']==40 and g['healthy']==40


def test_idempotency_under_repeated_requests(client, auth):
    client.post('/api/v1/applications',headers=auth,json=manifest('idem'))
    payload={'app_id':'app-idem','capability':'op.run','idempotency_key':'same-key'}
    ids={client.post('/api/v1/actions/execute',headers=auth,json=payload).json()['run_id'] for _ in range(30)}
    assert len(ids)==1


def test_queue_ids_unique_at_volume(client):
    ids={enqueue('noop',{'n':i})['id'] for i in range(100)}; assert len(ids)==100


def test_job_claim_is_exclusive(client):
    enqueue('noop',{'x':1}); first=claim('worker-a',lease_seconds=60); second=claim('worker-b',lease_seconds=60)
    assert first is not None and second is None


def test_distributed_lock_exclusive(client):
    assert acquire('leader','a',30) is True; assert acquire('leader','b',30) is False; assert release('leader','a') is True; assert acquire('leader','b',30) is True


def test_webhook_rejects_expired_timestamp(client, auth):
    client.post('/api/v1/applications',headers=auth,json=manifest('old'))
    secret=client.post('/api/v1/applications/app-old/webhook-secret/rotate',headers=auth).json()['secret']
    body=b'{"event_type":"aa","payload":{}}'; ts=str(int(time.time())-1000); sig=hmac.new(secret.encode(),ts.encode()+b'.'+body,hashlib.sha256).hexdigest()
    r=client.post('/api/v1/events/webhook/app-old',headers={'X-AIOC-Timestamp':ts,'X-AIOC-Signature':sig,'Content-Type':'application/json'},content=body); assert r.status_code==401


def test_duplicate_capabilities_are_rejected(client, auth):
    bad=manifest('dup'); bad['capabilities'].append({'name':'op.run','default_policy':'auto'})
    assert client.post('/api/v1/applications',headers=auth,json=bad).status_code==422


def test_nonexistent_org_registration_rejected(client, auth):
    assert client.post('/api/v1/applications',headers=auth,json=manifest('badorg','missing-org')).status_code==400


def test_invalid_api_key_is_unauthorized(client):
    assert client.get('/api/v1/applications',headers={'Authorization':'Bearer aioc_invalid'}).status_code==401


def test_policy_numeric_guard_boundary(client, auth):
    client.post('/api/v1/applications',headers=auth,json=manifest('limit'))
    client.post('/api/v1/policies',headers=auth,json={'app_id':'app-limit','capability':'op.run','level':'controlled','constraints':{'max_numeric':{'amount':10}}})
    assert client.post('/api/v1/actions/execute',headers=auth,json={'app_id':'app-limit','capability':'op.run','parameters':{'amount':10}}).json()['status']=='success'
    assert client.post('/api/v1/actions/execute',headers=auth,json={'app_id':'app-limit','capability':'op.run','parameters':{'amount':11}}).json()['status']=='blocked'


def test_resource_upsert_is_stable(client):
    first=upsert_resource(ResourceCreate(resource_type='server',external_id='same',name='A'))
    second=upsert_resource(ResourceCreate(resource_type='server',external_id='same',name='B',status='healthy'))
    assert first['id']==second['id'] and second['name']=='B'


def test_incident_fingerprint_deduplicates(client):
    a=open_incident(organization_id='global',title='Disk',description='90%',fingerprint='disk:x')
    b=open_incident(organization_id='global',title='Disk',description='95%',fingerprint='disk:x')
    assert a['id']==b['id']


def test_discover_contract(client, auth):
    client.post('/api/v1/applications',headers=auth,json=manifest('discover'))
    body=client.get('/api/v1/applications/app-discover/discover',headers=auth).json(); assert body['ok'] is True and len(body['capabilities'])==2


def test_webhook_secret_rotates_versions(client, auth):
    client.post('/api/v1/applications',headers=auth,json=manifest('rotate'))
    a=client.post('/api/v1/applications/app-rotate/webhook-secret/rotate',headers=auth).json(); b=client.post('/api/v1/applications/app-rotate/webhook-secret/rotate',headers=auth).json()
    assert b['version']==a['version']+1 and a['secret']!=b['secret']


def test_queue_validation_rejects_zero_attempts(client, auth):
    client.post('/api/v1/applications',headers=auth,json=manifest('qval'))
    r=client.post('/api/v1/actions/queue',headers=auth,json={'request':{'app_id':'app-qval','capability':'op.run'},'max_attempts':0}); assert r.status_code==422


def test_expired_job_lease_is_reclaimed_by_another_worker(client):
    from app.core.db import connect
    job=enqueue('noop',{'x':'lease'})
    first=claim('worker-old',lease_seconds=1); assert first and first['id']==job['id']
    with connect() as conn:
        conn.execute("UPDATE jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",(job['id'],))
    second=claim('worker-new',lease_seconds=30)
    assert second and second['id']==job['id'] and second['lease_owner']=='worker-new'


def test_scheduler_lock_takeover_after_expiry(client):
    from app.core.db import connect
    assert acquire('scheduler-ha','node-a',30) is True
    assert acquire('scheduler-ha','node-b',30) is False
    with connect() as conn:
        conn.execute("UPDATE distributed_locks SET lease_until='2000-01-01T00:00:00+00:00' WHERE name='scheduler-ha'")
    assert acquire('scheduler-ha','node-b',30) is True


def test_worker_stale_detection(client, monkeypatch):
    from app.services.workers import heartbeat,list_workers
    from app.core.db import connect
    heartbeat('dead-worker','host',999)
    with connect() as conn:
        conn.execute("UPDATE worker_nodes SET last_heartbeat='2000-01-01T00:00:00+00:00' WHERE id='dead-worker'")
    row=next(x for x in list_workers() if x['id']=='dead-worker')
    assert row['status']=='stale'


def test_snapshot_checksum_tamper_is_rejected(client,tmp_path):
    from app.services.snapshots import export_snapshot,restore_snapshot
    p=tmp_path/'tamper.json'; export_snapshot(str(p))
    p.write_text(p.read_text()+' ')
    import pytest
    with pytest.raises(ValueError,match='SHA-256 mismatch'):
        restore_snapshot(str(p),force=True)
