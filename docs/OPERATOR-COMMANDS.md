# Natural-language Operator Examples

The deterministic fallback understands named projects/servers for common Indonesian/English requests; a configured AI router handles broader wording but is still constrained by the same catalog.

Examples:

```text
cek kondisi server ubuntu-main
server ubuntu-main lambat, cari masalah server dan bereskan
perbaiki bug di Finance App
perbaiki Finance App dan onlinekan ke production
sinkronkan Finance App ke server produksi
cek repo Finance App
restart service netmon.service
```

Expected mapping:

- server troubleshooting -> `server.diagnose`
- code repair/development -> `project.develop`
- deploy/sync/online -> `project.deploy`
- repository status -> `project.inspect`
- one-shot infrastructure operation -> `agent.task`
- business application capability -> `application.action`

The planner cannot invent an agent/project/capability. Central validation rejects identifiers outside the registry before a task is queued.
