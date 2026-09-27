# AI Autonomous Operations Center (AIOC)

AIOC adalah pusat kendali AI global untuk aplikasi, server, repo developer, database, network, dan workflow operasional. Targetnya bukan chatbot: perintah bahasa sehari-hari diterjemahkan menjadi rencana terkontrol, dieksekusi melalui capability yang terdaftar, diverifikasi, dipulihkan/rollback bila perlu, lalu dicatat sebagai audit evidence.

## Status

**0.9.0-alpha.1 — M5 SERVER AUTONOMY + INFRASTRUCTURE SAFETY CHECKPOINT / NOT FINAL**

M4/M5 menyediakan jalur nyata `local repo -> autonomous developer -> verified artifact -> Ubuntu/server agent -> staged deployment -> activation -> health verification -> optional publish -> rollback`. Final GitHub UAT **belum dibuka** karena user meminta coding besar diselesaikan lebih dahulu. Checkpoint ini adalah rollback point yang sudah diregresi lokal, bukan production sign-off.

## Kapabilitas utama

### Global operations platform
- `Organization -> Business -> Location -> Application -> Resource` hierarchy
- RBAC/API key + OIDC/JWT federation
- encrypted secret vault
- capability registry + `READ/AUTO/CONTROLLED/APPROVAL/FORBIDDEN`
- durable queue, retry, lease reclaim, backpressure, worker heartbeat
- workflow DAG versioned, parallel bounded execution, timeout, approval resume, compensation
- incident/notification, operational memory, knowledge/runbook, resource graph
- connectors: Simulator, REST, GraphQL, mapped HTTP, Docker, Kubernetes, MQTT, SSH, SNMP
- notifications: log, webhook, Telegram, Slack, email, WhatsApp Cloud API
- Prometheus-style metrics, SSE, OpenTelemetry baseline, SLO
- logical backup/restore + SHA-256 + integrity verification

### Engineering + server autonomy
- **Local Developer Agent** polls outward to AIOC; cocok untuk PC/laptop di balik NAT
- **Ubuntu/Server Agent** dengan token terpisah dan capability allowlist
- project registry: repo path, runtime, build/test argv, artifact excludes
- autonomous developer session: context/read/search/unified patch/test/commit
- patch harus diverifikasi dengan test setelah perubahan sebelum dinyatakan selesai
- commit hanya men-stage file yang benar-benar disentuh AI; tidak memakai `git add -A`
- secret-like files (`.env`, key/certificate/credential paths) hard-denied dari read/search/patch/release artifact
- smart deployment state machine: inspect -> prepare -> checksum artifact -> stage -> activate -> health -> publish -> rollback
- previous stable release dipertahankan untuk compensating rollback
- autonomous server troubleshooting: status -> logs/service/docker inspection -> recovery aman -> verification
- service/container control **default-deny** sampai allowlist server dikonfigurasi
- planner tidak dapat menaikkan privilege: capability yang tidak dideklarasikan agent ditolak deterministik
- dashboard Engineering untuk agents, projects, targets, developer sessions, server sessions, deployments dan task queue


### M5 server autonomy & safety
- read-only diagnosis: process list, listening sockets, route table, firewall status
- package update discovery + security-update discovery tanpa arbitrary shell
- package apply membutuhkan infrastructure approval secara default, explicit package list, edge opt-in dan optional package allowlist
- infrastructure policy/approval terpisah dari application action policy
- artifact retention dengan proteksi artifact release aktif/staging
- server release cleanup menjaga `current` release dan minimum retained releases
- deployment/server automation failure membuka incident untuk escalation/notification
- project/target lifecycle disable control, agent token rotation dan infrastructure approval pada dashboard

## Cara berpikir AIOC

```text
Perintah user
   -> intent/planner
   -> registry + context
   -> permission/capability/policy
   -> bounded plan
   -> agent/tool execution
   -> verification
   -> retry / recovery / rollback
   -> audit + incident + evidence
```

Contoh tujuan bahasa biasa yang menjadi target:

- `perbaiki aplikasi keuangan, tes, commit, lalu onlinekan kalau aman`
- `sinkronkan Finance App ke server produksi`
- `server ubuntu-main lambat, cari masalah dan bereskan`
- `cek kondisi semua aplikasi`
- `restart service netmon` (hanya jika service ada di allowlist agent)

## Quick start central AIOC

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Worker:

```bash
python -m app.worker
```

Dashboard: `http://127.0.0.1:8000`

## Install Local/Server Agent

1. Register agent dari dashboard/API dan simpan token yang hanya ditampilkan sekali.
2. Tentukan capability dan filesystem/service/container allowlist.
3. Pada node Ubuntu/Linux:

```bash
export AIOC_CENTRAL_URL=https://aioc.example.com
export AIOC_AGENT_ID=ubuntu-main
export AIOC_AGENT_TOKEN='aioc_agent_...'
export AIOC_AGENT_CAPABILITIES='system.status,deploy.stage,deploy.activate,deploy.health,deploy.rollback,service.status,service.logs,service.restart'
export AIOC_AGENT_ALLOWED_ROOTS='/srv/apps'
export AIOC_AGENT_ALLOWED_SERVICES='netmon,tamasya,keuangan'
sudo -E bash scripts/install_agent.sh
```

Installer tidak memberikan sudo/root privilege otomatis. Izin systemd/Docker/nginx harus diberikan secara eksplisit sesuai resource yang memang boleh dikelola.

Untuk laptop developer, gunakan capability `repo.*` dan batasi `AIOC_AGENT_ALLOWED_ROOTS` hanya ke folder repo yang ingin Anda serahkan ke AIOC.

Lihat `docs/AGENT-DEPLOYMENT.md` dan `docs/OPERATOR-COMMANDS.md`.

## PostgreSQL/Docker

```bash
cp .env.example .env
# set strong secrets + explicit connector/notification host allowlist
docker compose up --build -d
```

Production tidak boleh memakai `AIOC_ALLOWED_CONNECTOR_HOSTS=*`.

## Disaster recovery

```bash
python scripts/backup_snapshot.py backups/aioc-snapshot.json
python scripts/restore_snapshot.py backups/aioc-snapshot.json
```

Restore menolak target non-empty kecuali dipaksa secara eksplisit dan memverifikasi checksum + post-restore integrity.

## Bukti lokal M4

- **134/134 automated tests PASS**
- legacy schema migration smoke -> schema **v13 PASS**
- real Uvicorn + separate worker smoke **PASS**
- live result: `{"apps":2,"incidents":1,"schema":12,"status":"PASS"}`
- local edge-agent temp Git repo patch/test/commit/path traversal/symlink/secret exclusion tests PASS
- deployment health failure -> previous release rollback regression PASS
- server capability escalation rejection PASS
- Python compile/static security/dashboard syntax/release checks dijalankan pada release gate

PostgreSQL/multi-runner/real-Ubuntu proof tetap akan dibuktikan pada final GitHub + infrastructure UAT setelah coding feature set dikunci.
