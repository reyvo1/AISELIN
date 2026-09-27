from __future__ import annotations

import asyncio
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any
from uuid import uuid4

import httpx

from app.core.config import settings
from app.core.db import connect, dumps, loads
from app.models import NotificationChannelCreate
from app.services.vault import get_secret
from app.connectors.security import require_allowed_target


def _now(): return datetime.now(timezone.utc).isoformat()


def create_channel(item: NotificationChannelCreate) -> dict:
    channel_id=str(uuid4()); now=_now(); cfg=item.config
    required={"webhook":["url"],"telegram":["chat_id"],"slack":["url"],"email":["host","port","from","to"],"whatsapp":["url","to"]}
    missing=[k for k in required.get(item.channel_type,[]) if not cfg.get(k)]
    if missing: raise ValueError(f"{item.channel_type} channel missing config: {', '.join(missing)}")
    with connect() as conn:
        conn.execute("""INSERT INTO notification_channels(id,organization_id,name,channel_type,config_json,secret_name,enabled,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,1,?,?)""",(channel_id,item.organization_id,item.name,item.channel_type,dumps(cfg),item.secret_name,now,now))
    return {"id":channel_id,**item.model_dump(mode="json"),"enabled":True}


def list_channels(organization_id: str | None=None) -> list[dict]:
    with connect() as conn:
        rows=conn.execute("SELECT * FROM notification_channels WHERE organization_id=? AND enabled=1 ORDER BY name",(organization_id,)).fetchall() if organization_id else conn.execute("SELECT * FROM notification_channels WHERE enabled=1 ORDER BY organization_id,name").fetchall()
    out=[]
    for row in rows:
        x=dict(row); x["config"]=loads(x.pop("config_json")); out.append(x)
    return out



def _secret(channel: dict) -> str | None:
    name=channel.get("secret_name")
    if not name: return None
    return get_secret(f"notification:{channel['id']}",name) or os.getenv(name)


def _incident_text(incident: dict[str,Any]) -> str:
    return f"[{incident.get('severity','info').upper()}] {incident.get('title','Incident')}\n{incident.get('description','')}\nApp: {incident.get('app_id') or 'global'}\nIncident: {incident.get('id','')}"


async def _send_email(channel: dict, incident: dict[str,Any], secret: str|None) -> dict[str,Any]:
    cfg=channel["config"]
    def send():
        msg=EmailMessage(); msg["Subject"]=cfg.get("subject_prefix","[AIOC]")+" "+incident.get("title","Incident"); msg["From"]=cfg["from"]
        tos=cfg["to"] if isinstance(cfg["to"],list) else [cfg["to"]]; msg["To"]=', '.join(tos); msg.set_content(_incident_text(incident))
        host=cfg["host"]; require_allowed_target(f"smtp://{host}"); port=int(cfg["port"]); timeout=float(cfg.get("timeout",10)); use_ssl=bool(cfg.get("ssl",False)); starttls=bool(cfg.get("starttls",not use_ssl))
        client=(smtplib.SMTP_SSL if use_ssl else smtplib.SMTP)(host,port,timeout=timeout)
        try:
            if starttls and not use_ssl: client.starttls()
            username=cfg.get("username")
            if username:
                if not secret: raise RuntimeError("email password secret is missing")
                client.login(username,secret)
            client.send_message(msg)
        finally: client.quit()
        return {"recipients":tos}
    return await asyncio.to_thread(send)


async def dispatch_incident(incident: dict[str, Any]) -> list[dict[str, Any]]:
    results=[]
    for channel in list_channels(incident["organization_id"]):
        status="success"; response: dict[str,Any]={}
        try:
            ctype=channel["channel_type"]; secret=_secret(channel)
            if ctype=="log": response={"logged":True}
            elif ctype in {"webhook","slack"}:
                url=channel["config"]["url"]
                require_allowed_target(url)
                headers={"Content-Type":"application/json"}
                if ctype=="webhook" and channel.get("secret_name"):
                    if not secret: raise RuntimeError("notification secret is missing")
                    headers["Authorization"]=f"Bearer {secret}"
                payload={"type":"incident","incident":incident} if ctype=="webhook" else {"text":_incident_text(incident)}
                async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                    r=await client.post(url,json=payload,headers=headers); r.raise_for_status(); response={"status_code":r.status_code}
            elif ctype=="telegram":
                if not secret: raise RuntimeError("telegram bot token secret is missing")
                url=f"https://api.telegram.org/bot{secret}/sendMessage"
                require_allowed_target(url)
                async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                    r=await client.post(url,json={"chat_id":channel["config"]["chat_id"],"text":_incident_text(incident)}); r.raise_for_status(); response={"status_code":r.status_code}
            elif ctype=="whatsapp":
                if not secret: raise RuntimeError("WhatsApp Cloud API token secret is missing")
                url=channel["config"]["url"]; require_allowed_target(url)
                payload={"messaging_product":"whatsapp","to":str(channel["config"]["to"]),"type":"text","text":{"body":_incident_text(incident)}}
                async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                    r=await client.post(url,json=payload,headers={"Authorization":f"Bearer {secret}","Content-Type":"application/json"}); r.raise_for_status(); response={"status_code":r.status_code}
            elif ctype=="email": response=await _send_email(channel,incident,secret)
            else: raise RuntimeError(f"unsupported notification channel: {ctype}")
        except Exception as exc:
            status="failed"; response={"error":str(exc)}
        notification_id=str(uuid4())
        with connect() as conn:
            conn.execute("INSERT INTO notifications(id,channel_id,incident_id,status,payload_json,response_json,created_at) VALUES(?,?,?,?,?,?,?)",(notification_id,channel["id"],incident["id"],status,dumps(incident),dumps(response),_now()))
        results.append({"id":notification_id,"channel_id":channel["id"],"status":status,"response":response})
    return results


def get_channel(channel_id: str) -> dict | None:
    with connect() as conn: row=conn.execute("SELECT * FROM notification_channels WHERE id=? AND enabled=1",(channel_id,)).fetchone()
    if not row: return None
    x=dict(row); x["config"]=loads(x.pop("config_json")); return x
