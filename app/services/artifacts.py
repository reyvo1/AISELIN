from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.db import connect, dumps, loads

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _root() -> Path:
    root = Path(settings.artifact_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _decode(row) -> dict[str, Any]:
    item = dict(row)
    item["metadata"] = loads(item.pop("metadata_json"))
    return item


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
    return _decode(row) if row else None


def list_artifacts(organization_id: str | None = None, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if organization_id is not None:
        where.append("organization_id=?"); params.append(organization_id)
    if project_id is not None:
        where.append("project_id=?"); params.append(project_id)
    sql = "SELECT * FROM artifacts" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_decode(row) for row in rows]


def store_artifact(*, organization_id: str, project_id: str | None, kind: str, filename: str, content: bytes, expected_sha256: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if len(content) > settings.artifact_max_bytes:
        raise ValueError("artifact exceeds configured size limit")
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        raise ValueError("artifact checksum mismatch")
    safe_name = _SAFE_NAME.sub("_", Path(filename).name)[:180] or "artifact.bin"
    artifact_id = str(uuid4())
    folder = _root() / organization_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{artifact_id}-{safe_name}"
    path.write_bytes(content)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO artifacts(id,organization_id,project_id,kind,filename,sha256,size_bytes,storage_path,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (artifact_id, organization_id, project_id, kind, safe_name, digest, len(content), str(path), dumps(metadata or {}), now),
        )
    return get_artifact(artifact_id) or {"id": artifact_id, "sha256": digest}


def read_artifact_bytes(artifact_id: str) -> bytes:
    item = get_artifact(artifact_id)
    if not item:
        raise ValueError("artifact not found")
    path = Path(item["storage_path"]).resolve()
    root = _root()
    if root not in path.parents:
        raise ValueError("artifact storage path escaped root")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise ValueError("artifact integrity check failed")
    return content


def prune_artifacts(*, organization_id: str, retention_days: int | None = None, max_per_project: int | None = None, dry_run: bool = True) -> dict[str, Any]:
    """Apply bounded retention without deleting artifacts referenced by active/staging releases."""
    from datetime import datetime, timedelta, timezone
    days = settings.artifact_retention_days if retention_days is None else max(1, min(int(retention_days), 3650))
    keep_n = settings.artifact_max_per_project if max_per_project is None else max(1, min(int(max_per_project), 1000))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with connect() as conn:
        protected_rows = conn.execute(
            "SELECT DISTINCT artifact_id FROM project_releases WHERE organization_id=? AND artifact_id IS NOT NULL AND status IN ('active','staging')",
            (organization_id,),
        ).fetchall()
        protected = {str(r['artifact_id']) for r in protected_rows}
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE organization_id=? ORDER BY COALESCE(project_id,''),created_at DESC",
            (organization_id,),
        ).fetchall()
    per_project: dict[str, int] = {}
    candidates: list[dict[str, Any]] = []
    for row in rows:
        item = _decode(row)
        if item['id'] in protected:
            continue
        key = str(item.get('project_id') or '__global__')
        per_project[key] = per_project.get(key, 0) + 1
        try:
            created = datetime.fromisoformat(str(item['created_at']))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except Exception:
            created = cutoff
        if per_project[key] > keep_n or created < cutoff:
            candidates.append(item)
    deleted: list[str] = []
    bytes_reclaimed = 0
    if not dry_run:
        for item in candidates:
            path = Path(item['storage_path']).resolve()
            root = _root()
            if path != root and root not in path.parents:
                continue
            size = int(item.get('size_bytes') or 0)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            with connect() as conn:
                conn.execute("DELETE FROM artifacts WHERE id=? AND organization_id=?", (item['id'], organization_id))
            deleted.append(item['id'])
            bytes_reclaimed += size
    return {
        'organization_id': organization_id,
        'dry_run': dry_run,
        'retention_days': days,
        'max_per_project': keep_n,
        'protected_count': len(protected),
        'candidate_count': len(candidates),
        'deleted_count': len(deleted),
        'bytes_reclaimed': bytes_reclaimed,
        'candidate_ids': [x['id'] for x in candidates[:500]],
        'deleted_ids': deleted[:500],
    }
