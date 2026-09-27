#!/usr/bin/env python3
from __future__ import annotations
import os, shutil, sys
from pathlib import Path
import httpx

required=["AIOC_CENTRAL_URL","AIOC_AGENT_ID","AIOC_AGENT_TOKEN","AIOC_AGENT_CAPABILITIES","AIOC_AGENT_ALLOWED_ROOTS"]
missing=[x for x in required if not os.getenv(x)]
if missing:
    print("preflight: FAIL missing="+",".join(missing)); raise SystemExit(2)
roots=[]
for raw in os.environ["AIOC_AGENT_ALLOWED_ROOTS"].split(","):
    p=Path(raw).expanduser()
    if not p.exists(): print(f"preflight: FAIL root does not exist: {p}"); raise SystemExit(2)
    roots.append(str(p.resolve()))
executables=[x.strip() for x in os.getenv("AIOC_AGENT_ALLOWED_EXECUTABLES","git,python,python3,pytest,npm,pnpm,yarn,composer,php").split(",") if x.strip()]
found={x:bool(shutil.which(x)) for x in executables}
capabilities={x.strip() for x in os.environ.get("AIOC_AGENT_CAPABILITIES","").split(",") if x.strip()}
required_tools={
    "system.processes":"ps", "network.sockets":"ss", "network.routes":"ip",
    "package.updates":"apt-get", "package.security_updates":"apt-get", "package.apply_updates":"apt-get",
    "docker.list":"docker", "docker.logs":"docker", "docker.restart":"docker",
}
missing_tools={cap:tool for cap,tool in required_tools.items() if cap in capabilities and not shutil.which(tool)}
if missing_tools:
    print(f"preflight: FAIL missing capability tools={missing_tools}"); raise SystemExit(2)
try:
    r=httpx.get(os.environ["AIOC_CENTRAL_URL"].rstrip("/")+"/api/v1/health",timeout=10); r.raise_for_status(); health=r.json()
except Exception as exc:
    print(f"preflight: FAIL central={exc}"); raise SystemExit(2)
print("preflight: PASS")
print({"agent_id":os.environ["AIOC_AGENT_ID"],"roots":roots,"executables":found,"capabilities":sorted(capabilities),"central_health":health})
