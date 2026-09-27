from __future__ import annotations

from datetime import datetime, timezone

from app.connectors.factory import build_connector
from app.core.db import connect, dumps, loads
from app.services.registry import registry


def _now(): return datetime.now(timezone.utc).isoformat()


def ensure_state(app_id: str, version: str="1") -> None:
    now=_now()
    with connect() as conn:
        conn.execute("""INSERT INTO connector_states(app_id,lifecycle_status,version,secret_version,last_validated_at,last_health_json,updated_at)
                        VALUES(?,'registered',?,0,NULL,'{}',?) ON CONFLICT(app_id) DO UPDATE SET version=excluded.version,updated_at=excluded.updated_at""",
                     (app_id,version,now))


async def validate(app_id: str) -> dict:
    manifest=registry.get(app_id)
    if not manifest: raise ValueError("application not found")
    health=await build_connector(manifest).health(); status="enabled" if health.get("ok") else "validation_failed"; now=_now()
    with connect() as conn:
        conn.execute("UPDATE connector_states SET lifecycle_status=?,last_validated_at=?,last_health_json=?,updated_at=? WHERE app_id=?",
                     (status,now,dumps(health),now,app_id))
    return {"app_id":app_id,"status":status,"health":health}


def get_state(app_id: str) -> dict | None:
    with connect() as conn: row=conn.execute("SELECT * FROM connector_states WHERE app_id=?",(app_id,)).fetchone()
    if not row: return None
    item=dict(row); item["last_health"]=loads(item.pop("last_health_json")); return item
