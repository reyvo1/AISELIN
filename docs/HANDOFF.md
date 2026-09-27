# AIOC M5 Handoff

## Baseline
- version: 0.9.0-alpha.1
- schema: v13
- regression: 134/134 PASS
- status: hardened checkpoint, NOT production-final

## Major working paths
- Central natural-language operator and policy engine
- Local Developer Agent: repo inspect/context/read/search/patch/test/commit/release
- Server Agent: status/service/docker/deploy/health/rollback/publish
- M5 diagnostics: process/socket/route/firewall/package/security updates
- Package mutation: approval by default + edge opt-in + optional package allowlist
- Infrastructure policy/approval is separate from app action policy
- Smart deployment protects clean revision, checksum artifact, secret config, health and compensating rollback
- Project/target concurrency locks
- Artifact retention and server release cleanup preserve active/current releases
- Failure escalation opens incidents and notification jobs

## Mandatory safety
Never give AI arbitrary root shell. Expand operations through bounded capabilities and allowlists only. Do not package `.env`/keys/credentials. Keep destructive infrastructure changes approval/forbidden by default.

## Next
See `docs/NEXT-GATE.md`.
