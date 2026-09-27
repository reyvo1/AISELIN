import os
import subprocess
import sys
from pathlib import Path


def test_upgrade_v4_shape_to_current(tmp_path):
    db=tmp_path/'old-v4.db'
    script=r'''
import sqlite3,sys
path=sys.argv[1]
c=sqlite3.connect(path)
c.executescript("""
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL);
INSERT INTO schema_migrations VALUES(1,'v1','x');
INSERT INTO schema_migrations VALUES(2,'v2','x');
INSERT INTO schema_migrations VALUES(3,'v3','x');
INSERT INTO schema_migrations VALUES(4,'v4','x');
CREATE TABLE applications(id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,connector_type TEXT NOT NULL,environment TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,manifest_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
INSERT INTO applications VALUES('legacy-app','Legacy','legacy','simulator','production',1,'{"id":"legacy-app","name":"Legacy","type":"legacy","organization_id":"global","connector":{"type":"simulator"},"capabilities":[{"name":"legacy.read","default_policy":"read"}]}','x','x');
CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload_json TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 3,available_at TEXT NOT NULL,lease_owner TEXT,lease_until TEXT,last_error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE workflow_runs(id TEXT PRIMARY KEY,workflow_id TEXT NOT NULL,organization_id TEXT NOT NULL,status TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,inputs_json TEXT NOT NULL,result_json TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT);
""")
c.commit();c.close()
'''
    subprocess.run([sys.executable,'-c',script,str(db)],check=True)
    verify=r'''
from app.core.db import init_db,connect
from app.core.migrations import schema_status
init_db()
assert schema_status()['current']==13
with connect() as c:
    app=c.execute("SELECT organization_id,business_id,location_id FROM applications WHERE id=?",('legacy-app',)).fetchone()
    assert app['organization_id']=='global'
    job_cols={r['name'] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
    run_cols={r['name'] for r in c.execute("PRAGMA table_info(workflow_runs)").fetchall()}
    assert 'organization_id' in job_cols
    assert {'cancel_requested','workflow_version','workflow_definition_json'} <= run_cols
print('upgrade-pass')
'''
    env=os.environ.copy();env.update({'AIOC_DATABASE_PATH':str(db),'AIOC_OWNER_TOKEN':'x','AIOC_MASTER_KEY':'y','AIOC_ENV':'development'})
    result=subprocess.run([sys.executable,'-c',verify],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr+result.stdout
    assert 'upgrade-pass' in result.stdout
