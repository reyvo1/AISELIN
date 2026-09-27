#!/usr/bin/env bash
set -euo pipefail

usage(){
  cat <<'USAGE'
Install the AIOC edge agent as a systemd service.

Required environment variables before running:
  AIOC_CENTRAL_URL
  AIOC_AGENT_ID
  AIOC_AGENT_TOKEN
  AIOC_AGENT_CAPABILITIES
  AIOC_AGENT_ALLOWED_ROOTS

Optional installer variables:
  INSTALL_DIR=/opt/aioc-agent
  ENV_FILE=/etc/aioc-agent.env
  SERVICE_NAME=aioc-agent
  AGENT_USER=<current user>
  AGENT_GROUP=<primary group of AGENT_USER>

Example local/developer node:
  export AIOC_CENTRAL_URL=https://aioc.example.com
  export AIOC_AGENT_ID=dev-laptop-01
  export AIOC_AGENT_TOKEN=aioc_agent_xxx
  export AIOC_AGENT_CAPABILITIES=system.status,repo.inspect,repo.context,repo.read,repo.search,repo.apply_patch,repo.run_steps,repo.commit,repo.prepare_release
  export AIOC_AGENT_ALLOWED_ROOTS=/home/me/Desktop/program
  sudo -E bash scripts/install_agent.sh

The installer does NOT grant sudo/root privileges to the agent. Server service/Docker/nginx permissions must be granted explicitly by the operator.
USAGE
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then usage; exit 0; fi

for v in AIOC_CENTRAL_URL AIOC_AGENT_ID AIOC_AGENT_TOKEN AIOC_AGENT_CAPABILITIES AIOC_AGENT_ALLOWED_ROOTS; do
  [[ -n "${!v:-}" ]] || { echo "Missing required environment variable: $v" >&2; usage; exit 2; }
done

INSTALL_DIR=${INSTALL_DIR:-/opt/aioc-agent}
ENV_FILE=${ENV_FILE:-/etc/aioc-agent.env}
SERVICE_NAME=${SERVICE_NAME:-aioc-agent}
AGENT_USER=${AGENT_USER:-${SUDO_USER:-$(id -un)}}
AGENT_GROUP=${AGENT_GROUP:-$(id -gn "$AGENT_USER")}
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

[[ $EUID -eq 0 ]] || { echo "Run installer with sudo/root so it can create the systemd unit." >&2; exit 2; }
getent passwd "$AGENT_USER" >/dev/null || { echo "Unknown AGENT_USER: $AGENT_USER" >&2; exit 2; }

install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "$ROOT_DIR/agent/runner.py" "$INSTALL_DIR/runner.py"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --disable-pip-version-check -r "$ROOT_DIR/agent/requirements.txt"
chown -R "$AGENT_USER:$AGENT_GROUP" "$INSTALL_DIR"

umask 077
cat > "$ENV_FILE" <<ENV
AIOC_CENTRAL_URL=${AIOC_CENTRAL_URL}
AIOC_AGENT_ID=${AIOC_AGENT_ID}
AIOC_AGENT_TOKEN=${AIOC_AGENT_TOKEN}
AIOC_AGENT_CAPABILITIES=${AIOC_AGENT_CAPABILITIES}
AIOC_AGENT_ALLOWED_ROOTS=${AIOC_AGENT_ALLOWED_ROOTS}
AIOC_AGENT_ALLOWED_EXECUTABLES=${AIOC_AGENT_ALLOWED_EXECUTABLES:-git,python,python3,pytest,npm,pnpm,yarn,composer,php}
AIOC_AGENT_ALLOWED_SERVICES=${AIOC_AGENT_ALLOWED_SERVICES:-}
AIOC_AGENT_ALLOWED_CONTAINERS=${AIOC_AGENT_ALLOWED_CONTAINERS:-}
AIOC_AGENT_ALLOW_PACKAGE_UPDATES=${AIOC_AGENT_ALLOW_PACKAGE_UPDATES:-false}
AIOC_AGENT_ALLOWED_PACKAGES=${AIOC_AGENT_ALLOWED_PACKAGES:-}
AIOC_AGENT_ALLOW_NGINX=${AIOC_AGENT_ALLOW_NGINX:-false}
AIOC_AGENT_NGINX_SITES_DIR=${AIOC_AGENT_NGINX_SITES_DIR:-/etc/nginx/conf.d}
AIOC_AGENT_ALLOWED_HEALTH_HOSTS=${AIOC_AGENT_ALLOWED_HEALTH_HOSTS:-127.0.0.1,localhost,::1}
AIOC_AGENT_POLL_SECONDS=${AIOC_AGENT_POLL_SECONDS:-3}
ENV
chmod 0600 "$ENV_FILE"

sed \
  -e "s|__AIOC_AGENT_USER__|$AGENT_USER|g" \
  -e "s|__AIOC_AGENT_GROUP__|$AGENT_GROUP|g" \
  -e "s|__AIOC_AGENT_INSTALL_DIR__|$INSTALL_DIR|g" \
  -e "s|__AIOC_AGENT_ENV_FILE__|$ENV_FILE|g" \
  "$ROOT_DIR/agent/systemd/aioc-agent.service" > "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"
systemctl --no-pager --full status "${SERVICE_NAME}.service" || true

echo "AIOC agent installed: ${SERVICE_NAME}.service"
echo "Agent ID: ${AIOC_AGENT_ID}"
echo "Environment file: ${ENV_FILE} (mode 0600)"
