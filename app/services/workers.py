from __future__ import annotations
from datetime import datetime,timezone,timedelta
from app.core.config import settings
from app.core.db import connect,dumps,loads

def _now(): return datetime.now(timezone.utc)
def heartbeat(worker_id:str,hostname:str,pid:int,status:str='running',metadata:dict|None=None)->None:
    now=_now().isoformat()
    with connect() as conn:
        conn.execute("""INSERT INTO worker_nodes(id,hostname,pid,status,started_at,last_heartbeat,metadata_json) VALUES(?,?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET status=excluded.status,last_heartbeat=excluded.last_heartbeat,metadata_json=excluded.metadata_json""",(worker_id,hostname,pid,status,now,now,dumps(metadata or {})))
def set_status(worker_id:str,status:str)->None:
    with connect() as conn: conn.execute("UPDATE worker_nodes SET status=?,last_heartbeat=? WHERE id=?",(status,_now().isoformat(),worker_id))
def list_workers()->list[dict]:
    cutoff=_now()-timedelta(seconds=settings.worker_stale_seconds)
    with connect() as conn:
        conn.execute("UPDATE worker_nodes SET status='stale' WHERE status='running' AND last_heartbeat<?",(cutoff.isoformat(),))
        rows=conn.execute("SELECT * FROM worker_nodes ORDER BY last_heartbeat DESC").fetchall()
    out=[]
    for r in rows:
        x=dict(r);x['metadata']=loads(x.pop('metadata_json'));out.append(x)
    return out
