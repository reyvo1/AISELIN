from __future__ import annotations
from typing import Any
from app.core.db import connect,loads


def verify_integrity() -> dict[str,Any]:
    errors=[];warnings=[]
    with connect() as conn:
        orgs={r['id'] for r in conn.execute("SELECT id FROM organizations WHERE enabled=1").fetchall()}
        apps=conn.execute("SELECT id,organization_id,business_id,location_id,manifest_json FROM applications WHERE enabled=1").fetchall()
        app_org={r['id']:r['organization_id'] for r in apps}
        businesses={r['id']:r['organization_id'] for r in conn.execute("SELECT id,organization_id FROM businesses WHERE enabled=1").fetchall()}
        locations={r['id']:(r['organization_id'],r['business_id']) for r in conn.execute("SELECT id,organization_id,business_id FROM locations WHERE enabled=1").fetchall()}
        for row in apps:
            aid=row['id'];org=row['organization_id']
            if org not in orgs:errors.append(f"application {aid} references missing organization {org}")
            if row['business_id'] and businesses.get(row['business_id'])!=org:errors.append(f"application {aid} business ownership mismatch")
            if row['location_id']:
                loc=locations.get(row['location_id'])
                if not loc or loc[0]!=org:errors.append(f"application {aid} location ownership mismatch")
                elif row['business_id'] and loc[1]!=row['business_id']:errors.append(f"application {aid} location/business mismatch")
            try:
                manifest=loads(row['manifest_json'])
                if manifest.get('organization_id','global')!=org:errors.append(f"application {aid} normalized organization differs from manifest")
            except Exception:errors.append(f"application {aid} manifest is invalid JSON")
        resources={r['id']:(r['organization_id'],r['app_id']) for r in conn.execute("SELECT id,organization_id,app_id FROM resources").fetchall()}
        for rid,(org,aid) in resources.items():
            if org not in orgs:errors.append(f"resource {rid} missing organization")
            if aid and app_org.get(aid)!=org:errors.append(f"resource {rid} app ownership mismatch")
        for e in conn.execute("SELECT id,organization_id,from_resource_id,to_resource_id FROM resource_edges").fetchall():
            left=resources.get(e['from_resource_id']);right=resources.get(e['to_resource_id'])
            if not left or not right:errors.append(f"resource edge {e['id']} has missing endpoint")
            elif left[0]!=e['organization_id'] or right[0]!=e['organization_id']:errors.append(f"resource edge {e['id']} crosses organization")
        for j in conn.execute("SELECT id,kind,payload_json,organization_id FROM jobs").fetchall():
            if j['organization_id'] not in orgs:errors.append(f"job {j['id']} missing organization")
            if j['kind']=='action':
                try:aid=loads(j['payload_json']).get('request',{}).get('app_id')
                except Exception:aid=None
                if aid and app_org.get(aid)!=j['organization_id']:errors.append(f"job {j['id']} app ownership mismatch")
        for inc in conn.execute("SELECT id,organization_id,app_id FROM incidents").fetchall():
            if inc['organization_id'] not in orgs:errors.append(f"incident {inc['id']} missing organization")
            if inc['app_id'] and app_org.get(inc['app_id'])!=inc['organization_id']:errors.append(f"incident {inc['id']} app ownership mismatch")
        for wf in conn.execute("SELECT id,organization_id,definition_json FROM workflows").fetchall():
            try:definition=loads(wf['definition_json'])
            except Exception:errors.append(f"workflow {wf['id']} definition invalid JSON");continue
            for step in definition.get('steps',[]):
                aid=step.get('app_id')
                if app_org.get(aid)!=wf['organization_id']:errors.append(f"workflow {wf['id']} step app ownership mismatch: {aid}")
        agents={r['id']:r['organization_id'] for r in conn.execute("SELECT id,organization_id FROM agent_nodes").fetchall()}
        for aid,org in agents.items():
            if org not in orgs:errors.append(f"agent {aid} references missing organization {org}")
        projects={r['id']:(r['organization_id'],r['source_agent_id']) for r in conn.execute("SELECT id,organization_id,source_agent_id FROM project_profiles").fetchall()}
        for pid,(org,source_agent) in projects.items():
            if org not in orgs:errors.append(f"project {pid} references missing organization {org}")
            if agents.get(source_agent)!=org:errors.append(f"project {pid} source agent ownership mismatch")
        targets={r['id']:(r['project_id'],r['organization_id'],r['server_agent_id']) for r in conn.execute("SELECT id,project_id,organization_id,server_agent_id FROM deployment_targets").fetchall()}
        for tid,(pid,org,server_agent) in targets.items():
            if projects.get(pid,(None,None))[0]!=org:errors.append(f"deployment target {tid} project ownership mismatch")
            if agents.get(server_agent)!=org:errors.append(f"deployment target {tid} server agent ownership mismatch")
        for task in conn.execute("SELECT id,organization_id,agent_id FROM agent_tasks").fetchall():
            if agents.get(task['agent_id'])!=task['organization_id']:errors.append(f"agent task {task['id']} agent ownership mismatch")
        for run in conn.execute("SELECT id,organization_id,project_id,target_id FROM deployment_runs").fetchall():
            org=run['organization_id']
            if projects.get(run['project_id'],(None,None))[0]!=org:errors.append(f"deployment run {run['id']} project ownership mismatch")
            if targets.get(run['target_id'],(None,None,None))[1]!=org:errors.append(f"deployment run {run['id']} target ownership mismatch")
        for session in conn.execute("SELECT id,organization_id,project_id,auto_deploy_target_id FROM developer_sessions").fetchall():
            org=session['organization_id']
            if projects.get(session['project_id'],(None,None))[0]!=org:errors.append(f"developer session {session['id']} project ownership mismatch")
            target_id=session['auto_deploy_target_id']
            if target_id and targets.get(target_id,(None,None,None))[1]!=org:errors.append(f"developer session {session['id']} target ownership mismatch")
        for session in conn.execute("SELECT id,organization_id,agent_id FROM server_sessions").fetchall():
            if agents.get(session['agent_id'])!=session['organization_id']:errors.append(f"server session {session['id']} agent ownership mismatch")
    return {'ok':not errors,'errors':errors,'warnings':warnings,'counts':{'errors':len(errors),'warnings':len(warnings)}}
