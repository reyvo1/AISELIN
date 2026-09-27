#!/usr/bin/env bash
set -euo pipefail

cleanup_generated(){
  rm -rf .pytest_cache
  find . -type d -name __pycache__ -not -path './.git/*' -prune -exec rm -rf {} + 2>/dev/null || true
  find . -type f -name '*.pyc' -not -path './.git/*' -delete 2>/dev/null || true
}
trap cleanup_generated EXIT
cleanup_generated

required=(README.md RELEASE.json pyproject.toml docs/ARCHITECTURE.md docs/SECURITY.md docs/CONNECTOR-SPEC.md docs/AGENT-DEPLOYMENT.md docs/OPERATOR-COMMANDS.md sdk/README.md app/main.py app/api/routes.py app/services/workflows.py app/services/snapshots.py app/services/developer.py app/services/server_ops.py app/services/deployments.py agent/runner.py agent/systemd/aioc-agent.service scripts/install_agent.sh deploy/helm/aioc/Chart.yaml)
for f in "${required[@]}"; do test -s "$f" || { echo "missing release file: $f"; exit 1; }; done
if find . -type d -name __pycache__ -not -path './.git/*' | grep -q .; then echo 'source cleanliness: FAIL (__pycache__ present)'; exit 1; fi
python -m compileall -q app agent sdk/python scripts
python scripts/security_static_check.py
bash -n scripts/install_agent.sh
bash scripts/install_agent.sh --help >/dev/null
pytest -q
if command -v php >/dev/null 2>&1; then php -l sdk/php/AiocConnector.php >/dev/null; fi
if command -v node >/dev/null 2>&1; then node --check sdk/node/aioc-connector.mjs; node --check app/static/app.js; fi
echo 'release-verify: PASS'
