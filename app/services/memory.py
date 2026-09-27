from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from app.core.db import connect,dumps,loads
from app.models import MemoryCreate

def _now(): return datetime.now(timezone.utc).isoformat()

def upsert_memory(item: MemoryCreate) -> dict:
    now=_now()
    with connect() as conn:
        if item.app_id is None:
            row=conn.execute(
                "SELECT id FROM operational_memory WHERE organization_id=? AND app_id IS NULL AND namespace=? AND memory_key=?",
                (item.organization_id,item.namespace,item.key),
            ).fetchone()
        else:
            row=conn.execute(
                "SELECT id FROM operational_memory WHERE organization_id=? AND app_id=? AND namespace=? AND memory_key=?",
                (item.organization_id,item.app_id,item.namespace,item.key),
            ).fetchone()
        mid=row["id"] if row else str(uuid4())
        if row:
            conn.execute("UPDATE operational_memory SET value_json=?,tags_json=?,updated_at=? WHERE id=?",(dumps(item.value),dumps(item.tags),now,mid))
        else:
            conn.execute("INSERT INTO operational_memory(id,organization_id,app_id,namespace,memory_key,value_json,tags_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(mid,item.organization_id,item.app_id,item.namespace,item.key,dumps(item.value),dumps(item.tags),now,now))
    return get_memory(mid) or {"id":mid}

def get_memory(mid: str) -> dict|None:
    with connect() as conn: row=conn.execute("SELECT * FROM operational_memory WHERE id=?",(mid,)).fetchone()
    if not row:return None
    x=dict(row);x["value"]=loads(x.pop("value_json"));x["tags"]=loads(x.pop("tags_json"));return x

def list_memory(organization_id: str,app_id: str|None=None,namespace: str|None=None,limit:int=200)->list[dict]:
    where=["organization_id=?"];params=[organization_id]
    if app_id is not None:where.append("app_id=?");params.append(app_id)
    if namespace:where.append("namespace=?");params.append(namespace)
    params.append(max(1,min(limit,500)))
    with connect() as conn:rows=conn.execute("SELECT id FROM operational_memory WHERE "+" AND ".join(where)+" ORDER BY updated_at DESC LIMIT ?",tuple(params)).fetchall()
    return [m for r in rows if (m:=get_memory(r["id"]))]
