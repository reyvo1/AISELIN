from __future__ import annotations

import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from sqlalchemy import inspect, text

from app.core.db import _get_engine, init_db, reset_database_for_tests
from app.core.migrations import CURRENT_SCHEMA_VERSION, schema_status


def main() -> None:
    engine=_get_engine()
    # This script is destructive by design and is only for disposable CI/UAT databases.
    reset_database_for_tests()
    with engine.begin() as c:
        c.execute(text("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL)"))
        for version in range(1,5):
            c.execute(text("INSERT INTO schema_migrations(version,name,applied_at) VALUES(:v,:n,'legacy')"),{"v":version,"n":f"legacy-v{version}"})
        c.execute(text("""CREATE TABLE applications(
          id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,connector_type TEXT NOT NULL,
          environment TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,manifest_json TEXT NOT NULL,
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"""))
        c.execute(text("""INSERT INTO applications(id,name,type,connector_type,environment,enabled,manifest_json,created_at,updated_at)
          VALUES('legacy-ci','Legacy CI','legacy','simulator','production',1,
          '{\"id\":\"legacy-ci\",\"name\":\"Legacy CI\",\"type\":\"legacy\",\"organization_id\":\"global\",\"connector\":{\"type\":\"simulator\"},\"capabilities\":[{\"name\":\"legacy.read\",\"default_policy\":\"read\"}]}','x','x')"""))
        c.execute(text("""CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload_json TEXT NOT NULL,status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 3,available_at TEXT NOT NULL,
          lease_owner TEXT,lease_until TEXT,last_error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"""))
        c.execute(text("""CREATE TABLE workflow_runs(id TEXT PRIMARY KEY,workflow_id TEXT NOT NULL,organization_id TEXT NOT NULL,
          status TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,inputs_json TEXT NOT NULL,result_json TEXT NOT NULL,
          started_at TEXT NOT NULL,finished_at TEXT)"""))
    init_db()
    status=schema_status(); assert status["current"]==CURRENT_SCHEMA_VERSION and status["up_to_date"]
    cols={x["name"] for x in inspect(engine).get_columns("applications")}; assert {"organization_id","business_id","location_id"} <= cols
    run_cols={x["name"] for x in inspect(engine).get_columns("workflow_runs")}; assert {"cancel_requested","workflow_version","workflow_definition_json"} <= run_cols
    assert "knowledge_documents" in inspect(engine).get_table_names()
    print(f"migration-upgrade-smoke: PASS backend={engine.dialect.name} schema={CURRENT_SCHEMA_VERSION}")
    reset_database_for_tests(); init_db()


if __name__ == "__main__": main()
