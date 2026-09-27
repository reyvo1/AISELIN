from __future__ import annotations
from datetime import datetime,timezone
from app.core.db import connect

def inc(name:str,amount:int=1)->None:
    now=datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("""INSERT INTO telemetry_counters(metric_key,value,updated_at) VALUES(?,?,?)
                     ON CONFLICT(metric_key) DO UPDATE SET value=telemetry_counters.value+?,updated_at=?""",(name,amount,now,amount,now))

def counters()->dict[str,int]:
    with connect() as conn:rows=conn.execute("SELECT metric_key,value FROM telemetry_counters ORDER BY metric_key").fetchall()
    return {r["metric_key"]:int(r["value"]) for r in rows}
