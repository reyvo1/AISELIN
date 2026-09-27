from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

BASE = os.getenv("AIOC_URL", "http://127.0.0.1:8000")
TOKEN = os.getenv("AIOC_OWNER_TOKEN", "change-me")
HEADERS = {"X-Owner-Token": TOKEN}
ROOT = Path(__file__).resolve().parents[1]

for name in ["hotel-simulator.json", "netmon-simulator.json"]:
    manifest = json.loads((ROOT / "examples" / name).read_text())
    response = httpx.post(f"{BASE}/api/v1/applications", json=manifest, headers=HEADERS, timeout=10)
    response.raise_for_status()
    print("registered", manifest["id"])

rule = json.loads((ROOT / "examples" / "router-offline-rule.json").read_text())
response = httpx.post(f"{BASE}/api/v1/rules", json=rule, headers=HEADERS, timeout=10)
response.raise_for_status()
print("registered autonomous rule", response.json()["id"])
