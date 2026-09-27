import asyncio

from app.services.incidents import open_incident
from app.services.jobs import get_job, list_jobs
from app.worker import process_one_job


def setup_org_admin(client, auth):
    client.post('/api/v1/organizations',headers=auth,json={'id':'org-x','name':'Org X','slug':'org-x'})
    client.post('/api/v1/principals',headers=auth,json={'id':'admin-x','name':'Admin X','kind':'human'})
    key=client.post('/api/v1/api-keys',headers=auth,json={'principal_id':'admin-x','organization_id':'org-x','role':'admin','label':'admin'}).json()['token']
    return {'Authorization':f'Bearer {key}'}


def test_business_location_application_hierarchy(client, auth):
    admin=setup_org_admin(client,auth)
    b=client.post('/api/v1/businesses',headers=admin,json={'id':'biz-x','organization_id':'org-x','name':'Business X'}); assert b.status_code==200
    l=client.post('/api/v1/locations',headers=admin,json={'id':'loc-x','organization_id':'org-x','business_id':'biz-x','name':'Manado','timezone':'Asia/Makassar','currency':'IDR','country_code':'ID'}); assert l.status_code==200
    app={'id':'hier-app','name':'Hierarchy App','type':'generic','organization_id':'org-x','business_id':'biz-x','location_id':'loc-x','connector':{'type':'simulator'},'capabilities':[{'name':'status.read','default_policy':'read'}]}
    assert client.post('/api/v1/applications',headers=admin,json=app).status_code==200
    got=client.get('/api/v1/applications',headers=admin).json()[0]; assert got['business_id']=='biz-x' and got['location_id']=='loc-x'


def test_location_must_match_business(client, auth):
    admin=setup_org_admin(client,auth)
    client.post('/api/v1/businesses',headers=admin,json={'id':'b1','organization_id':'org-x','name':'B1'})
    client.post('/api/v1/businesses',headers=admin,json={'id':'b2','organization_id':'org-x','name':'B2'})
    client.post('/api/v1/locations',headers=admin,json={'id':'l1','organization_id':'org-x','business_id':'b1','name':'L1'})
    app={'id':'bad-hier','name':'Bad Hier','type':'generic','organization_id':'org-x','business_id':'b2','location_id':'l1','connector':{'type':'simulator'},'capabilities':[{'name':'status.read'}]}
    assert client.post('/api/v1/applications',headers=admin,json=app).status_code==400


def test_log_notification_channel_delivers_incident(client, auth):
    channel=client.post('/api/v1/notification-channels',headers=auth,json={'organization_id':'global','name':'Ops Log','channel_type':'log','config':{}}); assert channel.status_code==200
    incident=open_incident(organization_id='global',title='CPU High',description='95%',fingerprint='notify-test')
    jobs=[j for j in list_jobs('queued') if j['kind']=='notify_incident' and j['payload'].get('incident_id')==incident['id']]
    assert jobs
    asyncio.run(process_one_job())
    assert get_job(jobs[0]['id'])['status']=='success'


def test_admin_channel_is_org_scoped(client, auth):
    admin=setup_org_admin(client,auth)
    assert client.post('/api/v1/notification-channels',headers=admin,json={'organization_id':'org-x','name':'Org Log','channel_type':'log'}).status_code==200
    assert client.post('/api/v1/notification-channels',headers=admin,json={'organization_id':'global','name':'Wrong','channel_type':'log'}).status_code==403


def test_whatsapp_channel_requires_cloud_api_target_and_recipient(client,auth):
    bad=client.post('/api/v1/notification-channels',headers=auth,json={'organization_id':'global','name':'wa','channel_type':'whatsapp','config':{}})
    assert bad.status_code==400
    ok=client.post('/api/v1/notification-channels',headers=auth,json={'organization_id':'global','name':'wa','channel_type':'whatsapp','config':{'url':'https://graph.example.test/v1/messages','to':'628123'},'secret_name':'WA_TOKEN'})
    assert ok.status_code==200
