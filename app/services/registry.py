from __future__ import annotations

from datetime import datetime, timezone

from app.core.db import connect, dumps, loads
from app.models import ApplicationManifest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistryService:
    def register(self, manifest: ApplicationManifest) -> ApplicationManifest:
        now = _now()
        with connect() as conn:
            org = conn.execute("SELECT id FROM organizations WHERE id=? AND enabled=1", (manifest.organization_id,)).fetchone()
            if not org:
                raise ValueError(f"organization not found: {manifest.organization_id}")
            if manifest.business_id:
                business = conn.execute("SELECT organization_id FROM businesses WHERE id=? AND enabled=1", (manifest.business_id,)).fetchone()
                if not business or business["organization_id"] != manifest.organization_id:
                    raise ValueError("business not found in organization")
            if manifest.location_id:
                location = conn.execute("SELECT organization_id,business_id FROM locations WHERE id=? AND enabled=1", (manifest.location_id,)).fetchone()
                if not location or location["organization_id"] != manifest.organization_id:
                    raise ValueError("location not found in organization")
                if manifest.business_id and location["business_id"] != manifest.business_id:
                    raise ValueError("location does not belong to application business")
            conn.execute(
                """INSERT INTO applications(id,name,type,connector_type,environment,organization_id,business_id,location_id,enabled,manifest_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,1,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,type=excluded.type,connector_type=excluded.connector_type,
                environment=excluded.environment,organization_id=excluded.organization_id,business_id=excluded.business_id,location_id=excluded.location_id,
                enabled=1,manifest_json=excluded.manifest_json,updated_at=excluded.updated_at""",
                (manifest.id, manifest.name, manifest.type, manifest.connector.type, manifest.environment, manifest.organization_id,manifest.business_id,manifest.location_id,
                 dumps(manifest.model_dump(mode="json")), now, now),
            )
        from app.services.lifecycle import ensure_state
        ensure_state(manifest.id, manifest.connector.version)
        return manifest

    def list(self, organization_id: str | None = None) -> list[ApplicationManifest]:
        with connect() as conn:
            rows = conn.execute("SELECT manifest_json FROM applications WHERE organization_id=? AND enabled=1 ORDER BY name",(organization_id,)).fetchall() if organization_id else conn.execute("SELECT manifest_json FROM applications WHERE enabled=1 ORDER BY name").fetchall()
        return [ApplicationManifest.model_validate(loads(r["manifest_json"])) for r in rows]

    def get(self, app_id: str) -> ApplicationManifest | None:
        with connect() as conn:
            row = conn.execute("SELECT manifest_json FROM applications WHERE id=? AND enabled=1", (app_id,)).fetchone()
        return ApplicationManifest.model_validate(loads(row["manifest_json"])) if row else None

    def disable(self, app_id: str) -> bool:
        with connect() as conn:
            cur = conn.execute("UPDATE applications SET enabled=0,updated_at=? WHERE id=?", (_now(), app_id))
            conn.execute("UPDATE connector_states SET lifecycle_status='disabled',updated_at=? WHERE app_id=?", (_now(), app_id))
        return cur.rowcount > 0

    def capability_exists(self, app_id: str, capability: str) -> bool:
        manifest = self.get(app_id)
        return bool(manifest and any(c.name == capability for c in manifest.capabilities))

    def capability(self, app_id: str, capability: str):
        manifest = self.get(app_id)
        if not manifest: return None
        return next((c for c in manifest.capabilities if c.name == capability), None)

registry = RegistryService()
