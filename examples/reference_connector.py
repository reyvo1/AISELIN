"""Reference connector server an existing production app can implement/adapt.
Run: CONNECTOR_TOKEN=secret uvicorn examples.reference_connector:app --port 8100
"""
from __future__ import annotations

import os
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="AIOC Reference Application Connector")
TOKEN = os.getenv("CONNECTOR_TOKEN", "change-connector-me")

class ExecuteRequest(BaseModel):
    capability: str
    parameters: dict = Field(default_factory=dict)
    dry_run: bool = False

@app.get("/health")
async def health():
    return {"ok": True, "service": "reference-connector"}

@app.post("/actions/execute")
async def execute(req: ExecuteRequest, authorization: str | None = Header(default=None)):
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "invalid connector token")
    # Map capability -> your application's service layer. Never map AI directly to raw SQL.
    if req.capability == "system.echo":
        return {"ok": True, "echo": req.parameters, "dry_run": req.dry_run}
    raise HTTPException(404, "capability not implemented")
