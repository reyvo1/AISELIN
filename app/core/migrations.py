from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import inspect, text
from app.core.db import _get_engine, connect, loads

CURRENT_SCHEMA_VERSION=13
MIGRATIONS={
  1:"foundation-registry-policy-audit",2:"durable-queue-rbac-vault-circuit",3:"resource-graph-incidents-ai-router",
  4:"global-hierarchy-notifications-security",5:"tenant-ownership-workflows-memory-observability",
  6:"application-tenant-columns-integrity-hardening",7:"worker-heartbeat-workflow-cancellation-queue-backpressure",
  8:"immutable-workflow-run-definition",9:"knowledge-runbook-layer",10:"edge-agent-project-deployment-operator",11:"autonomous-developer-sessions",12:"autonomous-server-operations-sessions",13:"infrastructure-policy-approvals",
}

def _now(): return datetime.now(timezone.utc).isoformat()
def _columns(table): return {c['name'] for c in inspect(_get_engine()).get_columns(table)}
def _add_column_if_missing(table,name,ddl):
    if name in _columns(table): return
    with _get_engine().begin() as raw: raw.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

def _apply_v5():
    for table in ("audit_events","approvals","rules","events","jobs"): _add_column_if_missing(table,"organization_id","TEXT NOT NULL DEFAULT 'global'")

def _apply_v6():
    _add_column_if_missing("applications","organization_id","TEXT NOT NULL DEFAULT 'global'"); _add_column_if_missing("applications","business_id","TEXT"); _add_column_if_missing("applications","location_id","TEXT")
    with connect() as conn:
        rows=conn.execute("SELECT id,manifest_json FROM applications").fetchall()
        for row in rows:
            try:m=loads(row['manifest_json'])
            except Exception:continue
            conn.execute("UPDATE applications SET organization_id=?,business_id=?,location_id=? WHERE id=?",(m.get('organization_id','global'),m.get('business_id'),m.get('location_id'),row['id']))
    with _get_engine().begin() as raw: raw.execute(text("CREATE INDEX IF NOT EXISTS idx_applications_org ON applications(organization_id,enabled)"))

def _apply_v7(): _add_column_if_missing("workflow_runs","cancel_requested","INTEGER NOT NULL DEFAULT 0")
def _apply_v8():
    _add_column_if_missing("workflow_runs","workflow_version","INTEGER NOT NULL DEFAULT 1")
    _add_column_if_missing("workflow_runs","workflow_definition_json","TEXT NOT NULL DEFAULT '{}'")
    # Best-effort backfill for runs created before immutable snapshots existed.
    with connect() as conn:
        rows=conn.execute("SELECT id,workflow_id,workflow_definition_json FROM workflow_runs").fetchall()
        for row in rows:
            if row['workflow_definition_json'] and row['workflow_definition_json']!='{}': continue
            wf=conn.execute("SELECT version,definition_json FROM workflows WHERE id=?",(row['workflow_id'],)).fetchone()
            if wf: conn.execute("UPDATE workflow_runs SET workflow_version=?,workflow_definition_json=? WHERE id=?",(wf['version'],wf['definition_json'],row['id']))

def apply_migrations():
    with connect() as conn: applied={int(r['version']) for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    for version,name in MIGRATIONS.items():
        if version in applied:continue
        if version==5:_apply_v5()
        elif version==6:_apply_v6()
        elif version==7:_apply_v7()
        elif version==8:_apply_v8()
        with connect() as conn:conn.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?) ON CONFLICT(version) DO NOTHING",(version,name,_now()))

def record_current_schema():apply_migrations()
def schema_status():
    with connect() as conn:rows=conn.execute("SELECT version,name,applied_at FROM schema_migrations ORDER BY version").fetchall()
    current=max([int(r['version']) for r in rows],default=0);return {'current':current,'expected':CURRENT_SCHEMA_VERSION,'up_to_date':current==CURRENT_SCHEMA_VERSION,'migrations':[dict(r) for r in rows]}
