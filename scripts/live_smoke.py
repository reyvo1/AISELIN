from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import httpx

BASE=os.getenv('AIOC_URL','http://127.0.0.1:8000').rstrip('/')
OWNER=os.getenv('AIOC_OWNER_TOKEN','change-me')
EVENT=os.getenv('AIOC_EVENT_TOKEN','change-event-me')
ROOT=Path(__file__).resolve().parents[1]
AUTH={'X-Owner-Token':OWNER}


def req(method,path,**kwargs):
    r=httpx.request(method,BASE+path,timeout=15,**kwargs); r.raise_for_status(); return r.json()


def wait_for_job(job_id, timeout=15):
    deadline=time.time()+timeout
    while time.time()<deadline:
        job=req('GET',f'/api/v1/jobs/{job_id}',headers=AUTH)
        if job['status'] in {'success','failed'}: return job
        time.sleep(.25)
    raise RuntimeError(f'job did not finish: {job_id}')


health=req('GET','/api/v1/health'); assert health['ok'] is True
for name in ['hotel-simulator.json','netmon-simulator.json']:
    manifest=json.loads((ROOT/'examples'/name).read_text())
    req('POST','/api/v1/applications',headers=AUTH,json=manifest)
rule=json.loads((ROOT/'examples'/'router-offline-rule.json').read_text())
req('POST','/api/v1/rules',headers=AUTH,json=rule)
g=req('GET','/api/v1/global/health',headers=AUTH); assert g['total']>=2 and g['healthy']>=2

e=req('POST','/api/v1/events',headers={'X-Event-Token':EVENT},json={'source_app_id':'netmon-demo','event_type':'router.offline','payload':{'offline_minutes':8,'router_id':'CI-R1'}})
assert e['fired'] and e['fired'][0]['result']['status']=='success'

refund=req('POST','/api/v1/actions/execute',headers=AUTH,json={'app_id':'hotel-demo','capability':'transaction.refund','parameters':{'amount':1000}})
assert refund['status']=='approval_required'
approved=req('POST',f"/api/v1/approvals/{refund['approval_id']}/approve",headers=AUTH); assert approved['result']['status']=='success'
forbidden=req('POST','/api/v1/actions/execute',headers=AUTH,json={'app_id':'hotel-demo','capability':'ledger.delete','parameters':{}}); assert forbidden['status']=='blocked'

queued=req('POST','/api/v1/actions/queue',headers=AUTH,json={'request':{'app_id':'netmon-demo','capability':'router.reconnect','parameters':{'router_id':'CI-R2'}},'max_attempts':2})
assert wait_for_job(queued['job']['id'])['status']=='success'

rot=req('POST','/api/v1/applications/netmon-demo/webhook-secret/rotate',headers=AUTH); secret=rot['secret']
body=json.dumps({'event_type':'router.alert','payload':{'severity':'high','title':'CI router alert','message':'simulated'}},separators=(',',':')).encode()
ts=str(int(time.time())); sig=hmac.new(secret.encode(),ts.encode()+b'.'+body,hashlib.sha256).hexdigest()
wh=req('POST','/api/v1/events/webhook/netmon-demo',content=body,headers={'Content-Type':'application/json','X-AIOC-Timestamp':ts,'X-AIOC-Signature':'sha256='+sig})
assert wh['event_id']


# Durable DAG workflow smoke
wf=req('POST','/api/v1/workflows',headers=AUTH,json={'organization_id':'global','name':'CI workflow','max_parallel_steps':2,'steps':[
    {'id':'status','app_id':'hotel-demo','capability':'hotel.status'},
    {'id':'reconnect','app_id':'netmon-demo','capability':'router.reconnect','depends_on':['status'],'parameters':{'router_id':'CI-WF-R1'}}
]})
wr=req('POST',f"/api/v1/workflows/{wf['id']}/run",headers=AUTH,json={'reason':'CI live workflow'})
assert wait_for_job(wr['job']['id'])['status']=='success'
run=req('GET',f"/api/v1/workflow-runs/{wr['run']['id']}",headers=AUTH); assert run['status']=='success'
mem=req('POST','/api/v1/memory',headers=AUTH,json={'organization_id':'global','app_id':'netmon-demo','namespace':'ci','key':'last-workflow','value':{'status':'pass'}}); assert mem['value']['status']=='pass'
metrics=httpx.get(BASE+'/api/v1/metrics',headers=AUTH,timeout=15); metrics.raise_for_status(); assert 'aioc_http_requests_total' in metrics.text
manifest=req('GET','/api/v1/system/snapshot-manifest',headers=AUTH); assert manifest['schema']['up_to_date'] is True


connectors=req('GET','/api/v1/connectors',headers=AUTH); assert {'rest','graphql','mapped_http','docker','kubernetes','mqtt','ssh','snmp'} <= set(connectors['available'])
knowledge=req('POST','/api/v1/knowledge/documents',headers=AUTH,json={'organization_id':'global','app_id':'netmon-demo','title':'CI router runbook','content':'heartbeat reconnect recovery router','source':'ci','tags':['router','recovery']}); assert knowledge['id']
search=req('GET','/api/v1/knowledge/search?q=router+recovery&app_id=netmon-demo',headers=AUTH); assert search['results'] and search['results'][0]['id']==knowledge['id']
integrity=req('GET','/api/v1/system/integrity',headers=AUTH); assert integrity['ok'] is True
slo=req('GET','/api/v1/telemetry/slo',headers=AUTH); assert 'error_rate' in slo and 'avg_latency_ms' in slo

# M4 engineering-autonomy central-plane smoke: edge nodes, project profile, deployment target,
# language planner and bounded server session. Edge runner behavior is separately exercised by pytest.
local=req('POST','/api/v1/agents',headers=AUTH,json={'id':'ci-local','organization_id':'global','name':'CI Local','kind':'local','capabilities':['system.status','repo.inspect','repo.prepare_release','repo.context','repo.read','repo.search','repo.apply_patch','repo.run_steps','repo.commit']})
server=req('POST','/api/v1/agents',headers=AUTH,json={'id':'ci-server','organization_id':'global','name':'CI Server','kind':'server','capabilities':['system.status','deploy.stage','deploy.activate','deploy.health','deploy.rollback']})
project=req('POST','/api/v1/projects',headers=AUTH,json={'id':'ci-project','organization_id':'global','name':'CI Project','source_agent_id':'ci-local','repo_path':'/workspace/ci-project','test_steps':[['pytest','-q']]}); assert project['id']=='ci-project'
target=req('POST','/api/v1/deployment-targets',headers=AUTH,json={'id':'ci-prod','project_id':'ci-project','organization_id':'global','server_agent_id':'ci-server','environment':'production','target_root':'/srv/ci-project','service_type':'none'}); assert target['id']=='ci-prod'
planned=req('POST','/api/v1/operator/command',headers=AUTH,json={'organization_id':'global','command':'sinkronkan CI Project ke server produksi','dry_run':True}); assert planned['plan'][0]['tool']=='project.deploy'
server_session=req('POST','/api/v1/server/sessions',headers=AUTH,json={'agent_id':'ci-server','objective':'cek server dan cari masalah','max_iterations':3})
agent_auth={'Authorization':'Bearer '+server['token']}
claimed=req('POST','/api/v1/agent/tasks/claim',headers=agent_auth)['task']; assert claimed and claimed['capability']=='system.status'
req('POST',f"/api/v1/agent/tasks/{claimed['id']}/complete",headers=agent_auth,json={'status':'success','result':{'ok':True,'load':{'1m':0.1}}})
server_state=req('GET',f"/api/v1/server/sessions/{server_session['id']}",headers=AUTH); assert server_state['status']=='blocked_ai'

summary=req('GET','/api/v1/operations/summary',headers=AUTH); assert summary['applications']>=2 and summary['open_incidents']>=1 and summary['agents']>=2 and summary['projects']>=1
info=req('GET','/api/v1/system/info',headers=AUTH); assert info['schema']['up_to_date'] is True
print(json.dumps({'status':'PASS','apps':summary['applications'],'incidents':summary['open_incidents'],'schema':info['schema']['current']},sort_keys=True))
