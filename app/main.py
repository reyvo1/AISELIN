from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.core.db import init_db
from app.core.middleware import ProductionSecurityMiddleware
from app.observability.otel import configure_otel


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.production and settings.owner_token == "change-me":
        raise RuntimeError("AIOC_OWNER_TOKEN must be changed in production")
    if settings.production and settings.event_token == "change-event-me":
        raise RuntimeError("AIOC_EVENT_TOKEN must be changed in production")
    if settings.production and settings.master_key == "development-master-key-change-me":
        raise RuntimeError("AIOC_MASTER_KEY must be changed in production")
    if settings.production and "*" in settings.allowed_connector_hosts:
        raise RuntimeError("AIOC_ALLOWED_CONNECTOR_HOSTS must be explicit in production")
    init_db()
    yield


app = FastAPI(title="AI Autonomous Operations Center", version="0.8.0-alpha.1", lifespan=lifespan)
configure_otel(app)
app.add_middleware(ProductionSecurityMiddleware)
app.include_router(router)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index(): return FileResponse(static_dir / "index.html")
