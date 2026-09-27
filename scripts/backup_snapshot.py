#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from app.core.db import init_db
from app.services.snapshots import export_snapshot

p=argparse.ArgumentParser(description='Create encrypted-at-rest logical AIOC disaster-recovery snapshot')
p.add_argument('output')
a=p.parse_args(); init_db(); print(json.dumps(export_snapshot(a.output),indent=2))
