# Next Macro Gate — M6 / Feature Freeze and Final UAT

Continue from **AIOC 0.9.0-alpha.1 M5**. Do not reopen architecture from zero.

M5 has implemented the requested engineering/server control plane: Local Developer Agent, Ubuntu/Server Agent, smart deployment/rollback, infrastructure policy approvals, bounded network/process/firewall/package diagnostics, artifact/release retention, incident escalation and dashboard controls.

Remaining gates before production sign-off:

1. freeze feature set unless real UAT exposes a missing production-critical capability
2. run final GitHub matrix on SQLite + PostgreSQL, including legacy migration -> schema v13, live API + worker, security/fault/load and agent protocol tests
3. verify source/ZIP manifest from the exact GitHub SHA
4. connect owner's Ubuntu server in read-only/preflight mode first
5. connect one local repository through Local Developer Agent and prove inspect/read/test without write
6. enable controlled repo write/commit only after read-only proof
7. connect one non-critical deployment target, prove stage -> activate -> health -> rollback
8. only then enable production application connectors/actions by capability and policy
9. production sign-off requires backup/restore drill, real service permissions, Nginx/SSL path and rollback proof on owner infrastructure

Do not grant arbitrary root shell. Add new server operations as bounded capabilities with explicit policy/approval.
