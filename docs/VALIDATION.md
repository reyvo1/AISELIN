# M5 Validation

Local evidence before packaging:

- `pytest -q`: **134/134 PASS**
- `bash scripts/release_verify.sh`: **PASS**
- `python scripts/migration_upgrade_smoke.py`: **PASS**, SQLite legacy v4 -> schema v13
- live Uvicorn + separate worker: **PASS**, schema v13
- infrastructure policy approval tests: PASS
- package update opt-in/allowlist tests: PASS
- artifact retention protects active release: PASS
- server release cleanup protects current release: PASS
- project/deployment target lifecycle controls: PASS
- existing deployment rollback, concurrent deployment lock, dirty repo, secret exclusion and artifact checksum gates remain PASS

PostgreSQL and real Ubuntu infrastructure evidence are intentionally deferred to final GitHub/infrastructure UAT and must not be inferred from local SQLite proof.
