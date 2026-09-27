# AIOC Status Matrix — 0.8.0-alpha.1 / M4

| Area | Status | Evidence / note |
|---|---|---|
| Dynamic app operations core | PASS local | Existing M3 regression retained |
| Tenant/RBAC/OIDC | PASS local | Regression suite |
| Durable jobs/workflows/incidents | PASS local | Regression suite |
| Connector/notification breadth | PASS local | Built-in conformance tests |
| Local Developer Agent protocol | PASS local | Agent token, heartbeat, task lease, repo tools |
| Real temp Git patch/test/commit | PASS local | `tests/test_edge_agent_runner.py` |
| Secret/symlink/path escape guard | PASS local | hard-deny regression |
| Smart deployment state machine | PASS local | inspect/package/stage/activate/health |
| Deployment rollback | PASS local | failed health -> previous active release |
| Autonomous Developer | PASS local | bounded planner + mandatory post-patch test |
| Autonomous Server Troubleshooter | PASS local | bounded status/log/restart/verify test |
| Planner privilege escalation guard | PASS local | unavailable server capability -> BLOCKED |
| Service/container default deny | PASS local | empty allowlists reject operations |
| Logical DR/integrity awareness of M4 records | PASS local | snapshot auto-enumeration + integrity scope |
| Schema migration | PASS SQLite | legacy v4 -> v12 |
| Real API + worker smoke | PASS SQLite | schema v12 live smoke |
| Full automated regression | PASS local | 116/116 |
| PostgreSQL M4 matrix | PENDING FINAL GITHUB UAT | deliberately deferred |
| Real user Ubuntu server permissions | PENDING INFRA UAT | requires actual server/systemd/Docker policy |
| Real user local repo integration | PENDING INTEGRATION UAT | connect after central final candidate |
| Production sign-off | NOT YET | no final claim before UAT |
