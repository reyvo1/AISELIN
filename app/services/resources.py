from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.core.db import connect, dumps, loads
from app.models import ResourceCreate, ResourceEdgeCreate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_resource(item: ResourceCreate) -> dict:
    now = _now()
    with connect() as conn:
        row = conn.execute("SELECT id FROM resources WHERE organization_id=? AND resource_type=? AND external_id=?",
                           (item.organization_id, item.resource_type, item.external_id)).fetchone()
        resource_id = row["id"] if row else str(uuid4())
        if row:
            conn.execute("UPDATE resources SET app_id=?,name=?,status=?,attributes_json=?,updated_at=? WHERE id=?",
                         (item.app_id, item.name, item.status, dumps(item.attributes), now, resource_id))
        else:
            conn.execute("INSERT INTO resources VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (resource_id, item.organization_id, item.app_id, item.resource_type, item.external_id,
                          item.name, item.status, dumps(item.attributes), now, now))
    return get_resource(resource_id) or {"id": resource_id}


def get_resource(resource_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM resources WHERE id=?", (resource_id,)).fetchone()
    if not row: return None
    item = dict(row); item["attributes"] = loads(item.pop("attributes_json")); return item


def list_resources(organization_id: str | None = None) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM resources WHERE organization_id=? ORDER BY resource_type,name", (organization_id,)).fetchall() if organization_id else conn.execute("SELECT * FROM resources ORDER BY organization_id,resource_type,name").fetchall()
    out=[]
    for row in rows:
        item=dict(row); item["attributes"]=loads(item.pop("attributes_json")); out.append(item)
    return out


def add_edge(item: ResourceEdgeCreate) -> dict:
    edge_id = str(uuid4())
    now = _now()
    with connect() as conn:
        endpoints=conn.execute("SELECT id,organization_id FROM resources WHERE id IN (?,?)",(item.from_resource_id,item.to_resource_id)).fetchall()
        by_id={r["id"]:r["organization_id"] for r in endpoints}
        if item.from_resource_id not in by_id or item.to_resource_id not in by_id:
            raise ValueError("resource edge endpoints must exist")
        if by_id[item.from_resource_id] != item.organization_id or by_id[item.to_resource_id] != item.organization_id:
            raise ValueError("cross-organization resource edges are forbidden")
        existing = conn.execute("SELECT id FROM resource_edges WHERE from_resource_id=? AND to_resource_id=? AND relation=?",
                                (item.from_resource_id,item.to_resource_id,item.relation)).fetchone()
        if existing:
            edge_id=existing["id"]
            conn.execute("UPDATE resource_edges SET attributes_json=? WHERE id=?", (dumps(item.attributes),edge_id))
        else:
            conn.execute("INSERT INTO resource_edges(id,organization_id,from_resource_id,to_resource_id,relation,attributes_json,created_at) VALUES(?,?,?,?,?,?,?)", (edge_id,item.organization_id,item.from_resource_id,item.to_resource_id,item.relation,dumps(item.attributes),now))
    return {"id":edge_id,**item.model_dump(mode="json")}


def impact(resource_id: str, max_depth: int = 4) -> dict:
    seen={resource_id}; frontier=[resource_id]; edges=[]
    with connect() as conn:
        for _ in range(max(1,min(max_depth,8))):
            nxt=[]
            for rid in frontier:
                rows=conn.execute("SELECT * FROM resource_edges WHERE from_resource_id=? OR to_resource_id=?", (rid,rid)).fetchall()
                for row in rows:
                    e=dict(row); e["attributes"]=loads(e.pop("attributes_json")); edges.append(e)
                    other=e["to_resource_id"] if e["from_resource_id"]==rid else e["from_resource_id"]
                    if other not in seen: seen.add(other); nxt.append(other)
            frontier=nxt
            if not frontier: break
    resources=[get_resource(rid) for rid in seen]
    return {"root":resource_id,"resources":[r for r in resources if r],"edges":edges}
