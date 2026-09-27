from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.db import connect


def _iso(dt=None): return (dt or datetime.now(timezone.utc)).isoformat()


def acquire(name: str, owner: str, lease_seconds: int=60) -> bool:
    now=datetime.now(timezone.utc); lease=now+timedelta(seconds=lease_seconds)
    with connect() as conn:
        row=conn.execute("SELECT owner,lease_until FROM distributed_locks WHERE name=?",(name,)).fetchone()
        if row and datetime.fromisoformat(row["lease_until"]) > now and row["owner"] != owner:
            return False
        conn.execute("""INSERT INTO distributed_locks(name,owner,lease_until,updated_at) VALUES(?,?,?,?)
                        ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,lease_until=excluded.lease_until,updated_at=excluded.updated_at""",
                     (name,owner,_iso(lease),_iso(now)))
    return True


def release(name: str, owner: str) -> bool:
    with connect() as conn: result=conn.execute("DELETE FROM distributed_locks WHERE name=? AND owner=?",(name,owner))
    return result.rowcount>0
