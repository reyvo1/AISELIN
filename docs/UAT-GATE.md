# Final GitHub UAT Gate — DEFERRED UNTIL FEATURE FREEZE

The owner explicitly wants major coding completed before final GitHub UAT. M4 therefore keeps this gate defined but not treated as final sign-off.

When feature freeze is declared, GitHub must prove at minimum:

1. complete regression suite on SQLite
2. complete regression suite on PostgreSQL
3. legacy migration -> current schema on both supported DB profiles
4. live Uvicorn + separate worker smoke on both profiles
5. Local/Server Agent protocol tests and package/source hygiene
6. developer repair safety, secret exclusion, deployment rollback and server capability-escalation gates
7. fault/load/security tests, lease recovery, scheduler/worker failover
8. release manifest/checksum verification

After GitHub is all green, infrastructure UAT must still verify real Ubuntu/systemd/Docker/Nginx permissions and actual application integrations before production sign-off.
