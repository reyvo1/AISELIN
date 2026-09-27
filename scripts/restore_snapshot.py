#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.core.db import init_db
from app.services.snapshots import restore_snapshot
p=argparse.ArgumentParser(description='Restore an AIOC logical snapshot to an initialized target database')
p.add_argument('snapshot');p.add_argument('--force',action='store_true',help='required when target contains operational data')
a=p.parse_args();init_db();print(json.dumps(restore_snapshot(a.snapshot,force=a.force),indent=2))
