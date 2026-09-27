from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from sqlalchemy import inspect
from app.core.db import _get_engine,connect
from app.core.migrations import schema_status
from app.services.integrity import verify_integrity

EXCLUDED={"rate_limits","distributed_locks","worker_nodes"}

def _json_safe(value:Any)->Any:
    if isinstance(value,(str,int,float,bool)) or value is None:return value
    if hasattr(value,'isoformat'):return value.isoformat()
    return str(value)

def export_snapshot(path:str)->dict[str,Any]:
    engine=_get_engine();inspector=inspect(engine);tables=[t for t in inspector.get_table_names() if t not in EXCLUDED]
    payload={'format':'aioc-logical-snapshot-v1','created_at':datetime.now(timezone.utc).isoformat(),'schema':schema_status(),'integrity':verify_integrity(),'tables':{}}
    with connect() as conn:
        for table in sorted(tables):
            rows=conn.execute(f"SELECT * FROM {table}").fetchall();payload['tables'][table]=[{k:_json_safe(v) for k,v in dict(r).items()} for r in rows]
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode();p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    digest=hashlib.sha256(raw).hexdigest();Path(str(p)+'.sha256').write_text(f"{digest}  {p.name}\n")
    return {'path':str(p),'sha256':digest,'tables':len(payload['tables']),'schema':payload['schema'],'integrity':payload['integrity']}

def snapshot_manifest()->dict[str,Any]:
    engine=_get_engine();inspector=inspect(engine);counts={}
    with connect() as conn:
        for table in sorted(t for t in inspector.get_table_names() if t not in EXCLUDED):counts[table]=int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()['n'])
    return {'format':'aioc-logical-snapshot-v1','schema':schema_status(),'integrity':verify_integrity(),'table_counts':counts}

def _verify_checksum(path:Path)->str:
    raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest();side=Path(str(path)+'.sha256')
    if side.exists():
        expected=side.read_text().strip().split()[0]
        if expected!=digest:raise ValueError('snapshot SHA-256 mismatch')
    return digest

def restore_snapshot(path:str,*,force:bool=False)->dict[str,Any]:
    p=Path(path);digest=_verify_checksum(p);payload=json.loads(p.read_text())
    if payload.get('format')!='aioc-logical-snapshot-v1':raise ValueError('unsupported snapshot format')
    source_schema=int(payload.get('schema',{}).get('current',0));target_schema=int(schema_status()['current'])
    if source_schema>target_schema:raise ValueError(f'snapshot schema {source_schema} is newer than target {target_schema}')
    inspector=inspect(_get_engine());target_tables=set(inspector.get_table_names());tables={k:v for k,v in payload.get('tables',{}).items() if k in target_tables and k!='schema_migrations'}
    protected=['applications','jobs','incidents','workflows','audit_events','api_keys','vault_secrets','agent_nodes','project_profiles','deployment_runs','developer_sessions','server_sessions']
    with connect() as conn:
        nonempty={t:int(conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()['n']) for t in protected if t in target_tables}
        nonempty={k:v for k,v in nonempty.items() if v}
        if nonempty and not force:raise ValueError(f'target is not empty; restore refused: {nonempty}')
        if force:
            for table in tables:conn.execute(f"DELETE FROM {table}")
        # Restore organizations first, then hierarchy/applications, then remaining tables.
        priority=['organizations','principals','businesses','locations','applications','agent_nodes','project_profiles','deployment_targets']
        ordered=priority+[t for t in tables if t not in priority]
        for table in ordered:
            rows=tables.get(table,[])
            if not rows:continue
            columns={c['name'] for c in inspector.get_columns(table)}
            for row in rows:
                data={k:v for k,v in row.items() if k in columns}
                keys=list(data);marks=','.join('?' for _ in keys);names=','.join(keys)
                conn.execute(f"INSERT INTO {table}({names}) VALUES({marks})",tuple(data[k] for k in keys))
    integrity=verify_integrity()
    if not integrity['ok']:raise ValueError(f"restored snapshot failed integrity: {integrity['errors'][:5]}")
    return {'restored':True,'sha256':digest,'source_schema':source_schema,'target_schema':target_schema,'integrity':integrity}
