from __future__ import annotations

import asyncio
import os
import signal
import socket
import time
from uuid import uuid4

from app.core.config import settings
from app.core.db import init_db
from app.models import ActionRequest
from app.services.executor import execute_action
from app.services.incidents import get_incident, open_incident
from app.services.jobs import claim, complete, fail
from app.services.notifications import dispatch_incident
from app.services.rules import run_due_schedules
from app.services.workflows import execute_run
from app.services.workers import heartbeat, set_status

WORKER_ID=f"{socket.gethostname()}-{os.getpid()}-{str(uuid4())[:8]}"
_STOP=False

def _request_stop(*_):
    global _STOP; _STOP=True

async def process_one_job() -> bool:
    job=claim(WORKER_ID)
    if not job:return False
    try:
        if job["kind"]=="action":
            req=ActionRequest.model_validate(job["payload"]["request"]);result=await execute_action(req)
            if result.status in {"success","dry_run","approval_required","blocked"}:complete(job["id"])
            else:
                updated=fail(job["id"],result.message)
                if updated and updated["status"]=="failed":open_incident(organization_id=job.get("organization_id","global"),app_id=req.app_id,severity="high",title="Durable job exhausted retries",description=result.message,fingerprint=f"job:{job['id']}",metadata={"job":updated})
        elif job["kind"]=="notify_incident":
            incident=get_incident(job["payload"].get("incident_id",""))
            if not incident:complete(job["id"])
            else:
                results=await dispatch_incident(incident);failures=[r for r in results if r["status"]=="failed"]
                if failures:fail(job["id"],str(failures))
                else:complete(job["id"])
        elif job["kind"]=="workflow":
            result=await execute_run(job["payload"].get("run_id",""))
            if result.get("status") in {"success","waiting_approval","cancelled"}:complete(job["id"])
            else:
                updated=fail(job["id"],result.get("result",{}).get("message","workflow failed"))
                if updated and updated["status"]=="failed":open_incident(organization_id=job.get("organization_id","global"),app_id=None,severity="high",title="Workflow exhausted retries",description=str(result.get("result",{})),fingerprint=f"workflow-job:{job['id']}",metadata={"job":updated,"run":result})
        else:fail(job["id"],f"unknown job kind: {job['kind']}")
        return True
    except Exception as exc:fail(job["id"],str(exc));return True

async def main() -> None:
    init_db();signal.signal(signal.SIGTERM,_request_stop);signal.signal(signal.SIGINT,_request_stop)
    heartbeat(WORKER_ID,socket.gethostname(),os.getpid(),metadata={"poll_seconds":settings.worker_poll_seconds});last=time.monotonic()
    try:
        while not _STOP:
            try:
                if time.monotonic()-last >= settings.worker_heartbeat_seconds:
                    heartbeat(WORKER_ID,socket.gethostname(),os.getpid());last=time.monotonic()
                await run_due_schedules(WORKER_ID);processed=await process_one_job()
                if not processed:await asyncio.sleep(settings.worker_poll_seconds)
            except Exception as exc:
                print(f"worker error: {exc}",flush=True);await asyncio.sleep(settings.worker_poll_seconds)
    finally:set_status(WORKER_ID,"stopped")

if __name__=="__main__":asyncio.run(main())
