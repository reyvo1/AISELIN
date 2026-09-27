def test_tenant_scoped_runbook_knowledge_search(client, auth):
    r=client.post('/api/v1/knowledge/documents',headers=auth,json={
        'organization_id':'global','app_id':None,'title':'MikroTik Router Recovery',
        'content':'If heartbeat is absent, run health check then reconnect. Never reset configuration automatically.',
        'source':'runbook','tags':['mikrotik','router','recovery']})
    assert r.status_code==200, r.text
    did=r.json()['id']
    s=client.get('/api/v1/knowledge/search',headers=auth,params={'q':'router heartbeat recovery'})
    assert s.status_code==200 and s.json()['results'][0]['id']==did
    assert s.json()['results'][0]['score']>0
    assert client.delete(f'/api/v1/knowledge/documents/{did}',headers=auth).status_code==200
