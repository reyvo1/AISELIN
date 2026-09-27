from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.db import connect
from app.services.telemetry import inc


def _rate_key(request: Request) -> str:
    auth=request.headers.get("authorization") or request.headers.get("x-owner-token") or ""
    if auth:
        return "auth:"+hashlib.sha256(auth.encode()).hexdigest()[:24]
    host=request.client.host if request.client else "unknown"
    return "ip:"+host


def _consume(key: str) -> tuple[bool,int]:
    if settings.rate_limit_per_minute <= 0: return True,0
    now=datetime.now(timezone.utc); window=now.strftime("%Y-%m-%dT%H:%M")
    with connect() as conn:
        row=conn.execute("""INSERT INTO rate_limits(key_id,window_id,count,updated_at) VALUES(?,?,1,?)
                            ON CONFLICT(key_id,window_id) DO UPDATE SET count=rate_limits.count+1,updated_at=excluded.updated_at
                            RETURNING count""",(key,window,now.isoformat())).fetchone()
    count=int(row["count"])
    return count <= settings.rate_limit_per_minute,count


class ProductionSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started=time.perf_counter()
        request_id=request.headers.get("X-Request-ID") or str(uuid4())
        if request.url.path.startswith("/api/"):
            length=request.headers.get("content-length")
            if length:
                try:
                    if int(length) > settings.max_request_bytes:
                        return JSONResponse({"detail":"request body too large"},status_code=413,headers={"X-Request-ID":request_id})
                except ValueError:
                    return JSONResponse({"detail":"invalid content-length"},status_code=400,headers={"X-Request-ID":request_id})
            allowed,count=_consume(_rate_key(request))
            if not allowed:
                return JSONResponse({"detail":"rate limit exceeded","count":count},status_code=429,headers={"X-Request-ID":request_id,"Retry-After":"60"})
        response=await call_next(request)
        if request.url.path.startswith("/api/"):
            try:
                inc("http.requests.total")
                inc(f"http.status.{response.status_code}")
                elapsed_us=max(0,int((time.perf_counter()-started)*1_000_000))
                inc("http.duration_us.count")
                inc("http.duration_us.sum",elapsed_us)
            except Exception:
                pass
        response.headers["X-Request-ID"]=request_id
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'"
        return response
