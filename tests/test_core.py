def register(client, auth, manifest):
    r = client.post('/api/v1/applications', json=manifest, headers=auth)
    assert r.status_code == 200, r.text


def test_health(client):
    r = client.get('/api/v1/health')
    assert r.status_code == 200
    assert r.json()['ok'] is True


def test_register_requires_owner(client, hotel_manifest):
    r = client.post('/api/v1/applications', json=hotel_manifest)
    assert r.status_code == 401


def test_register_and_list(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    rows = client.get('/api/v1/applications', headers=auth).json()
    assert rows[0]['id'] == 'hotel-test'


def test_global_health_dynamic(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    g = client.get('/api/v1/global/health', headers=auth).json()
    assert g['total'] == 1
    assert g['healthy'] == 1


def test_auto_or_controlled_action_executes(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    r = client.post('/api/v1/actions/execute', headers=auth, json={
        'app_id':'hotel-test','capability':'shift.close','parameters':{'shift_id':'S1'}
    })
    assert r.status_code == 200
    assert r.json()['status'] == 'success'


def test_forbidden_action_is_blocked(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    r = client.post('/api/v1/actions/execute', headers=auth, json={
        'app_id':'hotel-test','capability':'ledger.delete','parameters':{}
    })
    assert r.json()['status'] == 'blocked'


def test_undeclared_action_is_blocked(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    r = client.post('/api/v1/actions/execute', headers=auth, json={
        'app_id':'hotel-test','capability':'root.shell','parameters':{}
    })
    assert r.json()['status'] == 'blocked'


def test_approval_flow(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    first = client.post('/api/v1/actions/execute', headers=auth, json={
        'app_id':'hotel-test','capability':'transaction.refund','parameters':{'amount':1000}
    }).json()
    assert first['status'] == 'approval_required'
    approval_id = first['approval_id']
    pending = client.get('/api/v1/approvals?status=pending', headers=auth).json()
    assert len(pending) == 1
    approved = client.post(f'/api/v1/approvals/{approval_id}/approve', headers=auth)
    assert approved.status_code == 200
    assert approved.json()['result']['status'] == 'success'


def test_policy_override_and_constraint(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    p = client.post('/api/v1/policies', headers=auth, json={
        'app_id':'hotel-test','capability':'shift.close','level':'controlled',
        'constraints':{'max_numeric':{'age_hours':12}}
    })
    assert p.status_code == 200
    blocked = client.post('/api/v1/actions/execute', headers=auth, json={
        'app_id':'hotel-test','capability':'shift.close','parameters':{'age_hours':13}
    }).json()
    assert blocked['status'] == 'blocked'
    ok = client.post('/api/v1/actions/execute', headers=auth, json={
        'app_id':'hotel-test','capability':'shift.close','parameters':{'age_hours':12}
    }).json()
    assert ok['status'] == 'success'


def test_command_global_health(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    r = client.post('/api/v1/commands', headers=auth, json={'command':'cek semua aplikasi'})
    assert r.status_code == 200
    assert r.json()['mode'] == 'global_health'


def test_command_explicit_safe_parser(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    r = client.post('/api/v1/commands', headers=auth, json={
        'command':'run hotel-test hotel.status {"scope":"today"}'
    })
    assert r.status_code == 200
    assert r.json()['results'][0]['result']['status'] == 'success'


def test_audit_is_written(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    client.post('/api/v1/actions/execute', headers=auth, json={
        'app_id':'hotel-test','capability':'hotel.status','parameters':{}
    })
    rows = client.get('/api/v1/audit', headers=auth).json()
    assert len(rows) == 1
    assert rows[0]['capability'] == 'hotel.status'


def test_disable_application(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    assert client.delete('/api/v1/applications/hotel-test', headers=auth).status_code == 200
    assert client.get('/api/v1/applications', headers=auth).json() == []

def test_idempotency_returns_previous_result(client, auth, hotel_manifest):
    register(client, auth, hotel_manifest)
    payload = {
        'app_id':'hotel-test','capability':'hotel.status','parameters':{'scope':'today'},
        'idempotency_key':'status-2026-09-26'
    }
    first = client.post('/api/v1/actions/execute', headers=auth, json=payload).json()
    second = client.post('/api/v1/actions/execute', headers=auth, json=payload).json()
    assert first['status'] == 'success'
    assert second['status'] == 'success'
    assert second['run_id'] == first['run_id']
    assert 'idempotent replay' in second['message']

def test_global_health_requires_owner(client):
    assert client.get('/api/v1/global/health').status_code == 401
