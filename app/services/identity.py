from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from app.core.db import connect, dumps, loads
from app.models import ApiKeyCreate, OrganizationCreate, PrincipalCreate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_organization(item: OrganizationCreate) -> dict:
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO organizations(id,name,slug,metadata_json,enabled,created_at,updated_at)
               VALUES(?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,slug=excluded.slug,
               metadata_json=excluded.metadata_json,enabled=1,updated_at=excluded.updated_at""",
            (item.id, item.name, item.slug, dumps(item.metadata), now, now),
        )
    return item.model_dump(mode="json")


def get_organization(org_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM organizations WHERE id=? AND enabled=1", (org_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["metadata"] = loads(data.pop("metadata_json"))
    return data


def list_organizations() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM organizations WHERE enabled=1 ORDER BY name").fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["metadata"] = loads(item.pop("metadata_json"))
        out.append(item)
    return out


def create_principal(item: PrincipalCreate) -> dict:
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO principals(id,name,kind,enabled,metadata_json,created_at,updated_at)
               VALUES(?,?,?,1,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,
               enabled=1,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
            (item.id, item.name, item.kind, dumps(item.metadata), now, now),
        )
    return item.model_dump(mode="json")


def create_api_key(item: ApiKeyCreate) -> dict:
    token = "aioc_" + secrets.token_urlsafe(32)
    key_id = str(uuid4())
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect() as conn:
        principal = conn.execute("SELECT id FROM principals WHERE id=? AND enabled=1", (item.principal_id,)).fetchone()
        org = conn.execute("SELECT id FROM organizations WHERE id=? AND enabled=1", (item.organization_id,)).fetchone()
        if not principal:
            raise ValueError("principal not found")
        if not org:
            raise ValueError("organization not found")
        conn.execute(
            "INSERT INTO api_keys VALUES(?,?,?,?,?,?,?,?,?)",
            (key_id, item.principal_id, item.organization_id, item.role, item.label, digest, item.expires_at, None, _now()),
        )
    return {"id": key_id, "token": token, **item.model_dump(mode="json")}


def revoke_api_key(key_id: str) -> bool:
    with connect() as conn:
        result = conn.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (_now(), key_id))
    return result.rowcount > 0


def create_business(item) -> dict:
    now=_now()
    if not get_organization(item.organization_id): raise ValueError("organization not found")
    with connect() as conn:
        conn.execute("""INSERT INTO businesses(id,organization_id,name,metadata_json,enabled,created_at,updated_at)
                        VALUES(?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET organization_id=excluded.organization_id,
                        name=excluded.name,metadata_json=excluded.metadata_json,enabled=1,updated_at=excluded.updated_at""",
                     (item.id,item.organization_id,item.name,dumps(item.metadata),now,now))
    return item.model_dump(mode="json")


def list_businesses(organization_id: str | None=None) -> list[dict]:
    with connect() as conn:
        rows=conn.execute("SELECT * FROM businesses WHERE organization_id=? AND enabled=1 ORDER BY name",(organization_id,)).fetchall() if organization_id else conn.execute("SELECT * FROM businesses WHERE enabled=1 ORDER BY organization_id,name").fetchall()
    out=[]
    for row in rows:
        x=dict(row); x["metadata"]=loads(x.pop("metadata_json")); out.append(x)
    return out


def create_location(item) -> dict:
    now=_now()
    if not get_organization(item.organization_id): raise ValueError("organization not found")
    if item.business_id:
        with connect() as conn: business=conn.execute("SELECT id,organization_id FROM businesses WHERE id=? AND enabled=1",(item.business_id,)).fetchone()
        if not business or business["organization_id"] != item.organization_id: raise ValueError("business not found in organization")
    with connect() as conn:
        conn.execute("""INSERT INTO locations(id,organization_id,business_id,name,timezone,currency,country_code,metadata_json,enabled,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET organization_id=excluded.organization_id,
                        business_id=excluded.business_id,name=excluded.name,timezone=excluded.timezone,currency=excluded.currency,
                        country_code=excluded.country_code,metadata_json=excluded.metadata_json,enabled=1,updated_at=excluded.updated_at""",
                     (item.id,item.organization_id,item.business_id,item.name,item.timezone,item.currency,item.country_code,dumps(item.metadata),now,now))
    return item.model_dump(mode="json")


def list_locations(organization_id: str | None=None) -> list[dict]:
    with connect() as conn:
        rows=conn.execute("SELECT * FROM locations WHERE organization_id=? AND enabled=1 ORDER BY name",(organization_id,)).fetchall() if organization_id else conn.execute("SELECT * FROM locations WHERE enabled=1 ORDER BY organization_id,name").fetchall()
    out=[]
    for row in rows:
        x=dict(row); x["metadata"]=loads(x.pop("metadata_json")); out.append(x)
    return out
