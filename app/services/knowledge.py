from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from app.core.db import connect, dumps, loads
from app.models import KnowledgeDocumentCreate

_TOKEN=re.compile(r"[a-zA-Z0-9_.:-]{2,}")

def _now(): return datetime.now(timezone.utc).isoformat()
def _decode(row):
    x=dict(row); x["tags"]=loads(x.pop("tags_json")); x["metadata"]=loads(x.pop("metadata_json")); return x

def create_document(item: KnowledgeDocumentCreate) -> dict:
    did=str(uuid4()); now=_now()
    with connect() as conn:
        conn.execute("INSERT INTO knowledge_documents(id,organization_id,app_id,title,content,source,tags_json,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (did,item.organization_id,item.app_id,item.title,item.content,item.source,dumps(item.tags),dumps(item.metadata),now,now))
    return get_document(did) or {"id":did}

def get_document(document_id: str) -> dict|None:
    with connect() as conn: row=conn.execute("SELECT * FROM knowledge_documents WHERE id=?",(document_id,)).fetchone()
    return _decode(row) if row else None

def delete_document(document_id: str) -> bool:
    with connect() as conn: result=conn.execute("DELETE FROM knowledge_documents WHERE id=?",(document_id,))
    return bool(result.rowcount)

def list_documents(organization_id: str,app_id: str|None=None,limit:int=100) -> list[dict]:
    limit=max(1,min(limit,500)); where=["organization_id=?"]; params=[organization_id]
    if app_id is not None: where.append("app_id=?"); params.append(app_id)
    params.append(limit)
    with connect() as conn: rows=conn.execute("SELECT * FROM knowledge_documents WHERE "+" AND ".join(where)+" ORDER BY updated_at DESC LIMIT ?",tuple(params)).fetchall()
    return [_decode(r) for r in rows]

def search_documents(organization_id: str,query: str,app_id: str|None=None,limit:int=10) -> list[dict]:
    """Deterministic local retrieval baseline; an embedding plugin can replace/rerank this later."""
    terms={t.lower() for t in _TOKEN.findall(query)}
    if not terms: return []
    docs=list_documents(organization_id,app_id,500); ranked=[]
    for doc in docs:
        title=doc["title"].lower(); content=doc["content"].lower(); tags=" ".join(doc["tags"]).lower()
        score=sum(4 for t in terms if t in title)+sum(2 for t in terms if t in tags)+sum(1 for t in terms if t in content)
        if score: ranked.append((score,doc))
    ranked.sort(key=lambda x:(-x[0],x[1]["title"].lower()))
    out=[]
    for score,doc in ranked[:max(1,min(limit,50))]:
        snippet=doc["content"][:1200]
        out.append({**doc,"score":score,"snippet":snippet})
    return out
