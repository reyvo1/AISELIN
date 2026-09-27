def test_security_headers_and_request_id(client):
    r=client.get('/api/v1/health'); assert r.status_code==200
    assert r.headers['X-Content-Type-Options']=='nosniff' and r.headers['X-Frame-Options']=='DENY' and r.headers.get('X-Request-ID')


def test_schema_marker_and_system_info(client, auth):
    info=client.get('/api/v1/system/info',headers=auth).json(); assert info['schema']['up_to_date'] is True and info['schema']['current']==13


def test_operations_summary(client, auth, hotel_manifest):
    client.post('/api/v1/applications',headers=auth,json=hotel_manifest)
    client.post('/api/v1/actions/execute',headers=auth,json={'app_id':'hotel-test','capability':'hotel.status'})
    s=client.get('/api/v1/operations/summary',headers=auth).json(); assert s['applications']==1 and s['audit_events']>=1


def test_oversized_request_rejected(client, auth):
    r=client.post('/api/v1/commands',headers={**auth,'Content-Length':'2000000'},content=b'{}'); assert r.status_code==413
