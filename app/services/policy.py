from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.core.db import connect, dumps, loads
from app.models import ApplicationManifest, PolicyCreate, PolicyLevel


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PolicyService:
    def upsert(self, item: PolicyCreate) -> dict:
        now = _now()
        with connect() as conn:
            row = conn.execute(
                "SELECT id FROM policies WHERE COALESCE(app_id,'')=COALESCE(?,'') AND capability=?",
                (item.app_id, item.capability),
            ).fetchone()
            policy_id = row["id"] if row else str(uuid4())
            if row:
                conn.execute("UPDATE policies SET level=?,constraints_json=?,enabled=1,updated_at=? WHERE id=?",
                             (item.level.value, dumps(item.constraints), now, policy_id))
            else:
                conn.execute("INSERT INTO policies VALUES(?,?,?,?,?,1,?,?)",
                             (policy_id, item.app_id, item.capability, item.level.value, dumps(item.constraints), now, now))
        return {"id": policy_id, **item.model_dump(mode="json")}

    def resolve(self, manifest: ApplicationManifest, capability: str) -> tuple[PolicyLevel, dict]:
        with connect() as conn:
            row = conn.execute(
                """SELECT level,constraints_json FROM policies
                   WHERE enabled=1 AND capability=? AND (app_id=? OR app_id IS NULL)
                   ORDER BY CASE WHEN app_id=? THEN 0 ELSE 1 END LIMIT 1""",
                (capability, manifest.id, manifest.id),
            ).fetchone()
        if row:
            return PolicyLevel(row["level"]), loads(row["constraints_json"])
        cap = next((c for c in manifest.capabilities if c.name == capability), None)
        if not cap:
            return PolicyLevel.FORBIDDEN, {"reason": "capability not declared"}
        return cap.default_policy, {}

    def list(self) -> list[dict]:
        with connect() as conn:
            rows = conn.execute("SELECT * FROM policies WHERE enabled=1 ORDER BY app_id,capability").fetchall()
        return [{**dict(r), "constraints": loads(r["constraints_json"])} for r in rows]

policy_service = PolicyService()
