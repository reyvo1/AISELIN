from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.db import connect, dumps, loads


def _now_dt() -> datetime: return datetime.now(timezone.utc)
def _iso(dt: datetime | None = None) -> str: return (dt or _now_dt()).isoformat()


def _infer_org(kind: str, payload: dict[str, Any], explicit: str | None) -> str:
    if explicit: return explicit
    app_id = payload.get("request", {}).get("app_id") if kind == "action" else payload.get("app_id")
    if app_id:
        from app.services.registry import registry
        manifest=registry.get(app_id)
        if manifest: return manifest.organization_id
    if payload.get("organization_id"): return str(payload["organization_id"])
    return "global"


def enqueue(kind: str, payload: dict[str, Any], max_attempts: int | None = None, delay_seconds: int = 0, organization_id: str | None = None) -> dict:
    job_id=str(uuid4()); now=_now_dt(); available=now+timedelta(seconds=max(0,delay_seconds)); org=_infer_org(kind,payload,organization_id)
    with connect() as conn:
        queued=int(conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE organization_id=? AND status IN ('queued','running')",(org,)).fetchone()["n"])
        if settings.max_queued_jobs_per_org > 0 and queued >= settings.max_queued_jobs_per_org:
            raise RuntimeError(f"organization queue limit reached: {settings.max_queued_jobs_per_org}")
        conn.execute("""INSERT INTO jobs(id,kind,payload_json,status,attempts,max_attempts,available_at,lease_owner,lease_until,last_error,created_at,updated_at,organization_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (job_id,kind,dumps(payload),"queued",0,max_attempts or settings.max_action_attempts,available.isoformat(),None,None,None,now.isoformat(),now.isoformat(),org))
    return get_job(job_id) or {"id":job_id}


def _decode(row) -> dict:
    item=dict(row); item["payload"]=loads(item.pop("payload_json")); return item


def get_job(job_id: str) -> dict | None:
    with connect() as conn: row=conn.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
    return _decode(row) if row else None


def list_jobs(status: str | None=None, limit: int=100, organization_id: str | None=None) -> list[dict]:
    limit=max(1,min(limit,500)); where=[]; params=[]
    if status: where.append("status=?"); params.append(status)
    if organization_id is not None: where.append("organization_id=?"); params.append(organization_id)
    sql="SELECT * FROM jobs"+(" WHERE "+" AND ".join(where) if where else "")+" ORDER BY created_at DESC LIMIT ?"; params.append(limit)
    with connect() as conn: rows=conn.execute(sql,tuple(params)).fetchall()
    return [_decode(r) for r in rows]


def claim(worker_id: str, lease_seconds: int | None=None) -> dict | None:
    lease_seconds=lease_seconds or settings.worker_lease_seconds; now=_now_dt(); lease=now+timedelta(seconds=lease_seconds)
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='queued',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE status='running' AND lease_until<?",(_iso(now),_iso(now)))
        row=conn.execute("SELECT id FROM jobs WHERE status='queued' AND available_at<=? ORDER BY available_at,created_at LIMIT 1",(_iso(now),)).fetchone()
        if not row: return None
        result=conn.execute("UPDATE jobs SET status='running',lease_owner=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=? AND status='queued'",(worker_id,_iso(lease),_iso(now),row["id"]))
        if result.rowcount != 1: return None
    return get_job(row["id"])


def complete(job_id: str) -> None:
    with connect() as conn: conn.execute("UPDATE jobs SET status='success',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(_iso(),job_id))


def fail(job_id: str, error: str) -> dict | None:
    job=get_job(job_id)
    if not job: return None
    terminal=int(job["attempts"]) >= int(job["max_attempts"]); status="failed" if terminal else "queued"
    delay=min(300,2 ** max(1,int(job["attempts"]))); available=_now_dt()+timedelta(seconds=delay)
    with connect() as conn:
        conn.execute("UPDATE jobs SET status=?,available_at=?,lease_owner=NULL,lease_until=NULL,last_error=?,updated_at=? WHERE id=?",(status,_iso(available),error[:2000],_iso(),job_id))
    return get_job(job_id)


def retry_job(job_id: str) -> dict | None:
    job=get_job(job_id)
    if not job or job["status"] not in {"failed","cancelled"}: return None
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='queued',attempts=0,available_at=?,lease_owner=NULL,lease_until=NULL,last_error=NULL,updated_at=? WHERE id=?",(_iso(),_iso(),job_id))
    return get_job(job_id)


def cancel_job(job_id: str) -> dict | None:
    job=get_job(job_id)
    if not job or job["status"] in {"success","cancelled"}: return None
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",(_iso(),job_id))
    return get_job(job_id)
