def register_netmon(client, auth):
    manifest = {
        'id':'netmon-test','name':'NetMon Test','type':'network-management','environment':'production',
        'connector':{'type':'simulator'},
        'capabilities':[
            {'name':'router.reconnect','default_policy':'auto'},
            {'name':'router.reset','default_policy':'forbidden'}
        ]
    }
    assert client.post('/api/v1/applications', json=manifest, headers=auth).status_code == 200


def test_event_rule_executes(client, auth):
    register_netmon(client, auth)
    rule = {
        'name':'recover router','trigger_type':'event',
        'trigger':{'event_type':'router.offline','source_app_id':'netmon-test'},
        'condition':{'offline_minutes':{'gte':5}},
        'action':{'app_id':'netmon-test','capability':'router.reconnect','parameters':{'router_id':'${event.router_id}'}},
        'cooldown_seconds':300
    }
    assert client.post('/api/v1/rules', json=rule, headers=auth).status_code == 200
    r = client.post('/api/v1/events', headers={'X-Event-Token':'test-event'}, json={
        'source_app_id':'netmon-test','event_type':'router.offline','payload':{'offline_minutes':7,'router_id':'R1'}
    })
    body=r.json()
    assert len(body['fired']) == 1
    assert body['fired'][0]['result']['status'] == 'success'
    assert body['fired'][0]['result']['data']['parameters']['router_id'] == 'R1'


def test_event_rule_condition_prevents_action(client, auth):
    register_netmon(client, auth)
    rule = {
        'name':'recover router','trigger_type':'event',
        'trigger':{'event_type':'router.offline','source_app_id':'netmon-test'},
        'condition':{'offline_minutes':{'gte':5}},
        'action':{'app_id':'netmon-test','capability':'router.reconnect','parameters':{}}
    }
    client.post('/api/v1/rules', json=rule, headers=auth)
    r = client.post('/api/v1/events', headers={'X-Event-Token':'test-event'}, json={
        'source_app_id':'netmon-test','event_type':'router.offline','payload':{'offline_minutes':2}
    })
    assert r.json()['fired'] == []


def test_rule_cannot_target_forbidden_unknown_capability(client, auth):
    register_netmon(client, auth)
    rule = {
        'name':'bad rule','trigger_type':'event','trigger':{'event_type':'x'},
        'action':{'app_id':'netmon-test','capability':'does.not.exist','parameters':{}}
    }
    r=client.post('/api/v1/rules', json=rule, headers=auth)
    assert r.status_code == 400

def test_event_ingest_requires_event_token(client, auth):
    register_netmon(client, auth)
    r = client.post('/api/v1/events', json={
        'source_app_id':'netmon-test','event_type':'router.offline','payload':{}
    })
    assert r.status_code == 401
