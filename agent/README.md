# AIOC Edge Agent

The same lightweight runner can operate as a **Local Developer Agent** or **Ubuntu Server Agent**. It always initiates outbound HTTPS polling to AIOC; the central controller does not need inbound SSH access to the node.

## Local developer capability set

`system.status,repo.inspect,repo.context,repo.read,repo.search,repo.apply_patch,repo.run_steps,repo.commit,repo.prepare_release`

The agent can only access paths under `AIOC_AGENT_ALLOWED_ROOTS`. Repository edits use `git apply` without a shell, reject path traversal and `.git` changes, and autonomous developer sessions require a clean Git baseline.

## Server capability set

`system.status,deploy.stage,deploy.activate,deploy.health,deploy.rollback,service.status,service.restart`

Add `publish.nginx` only when the host is intentionally prepared for AIOC-managed reverse proxy configuration. It is disabled at runtime unless `AIOC_AGENT_ALLOW_NGINX=true`.

The server agent deploys immutable release directories and switches a `current` symlink atomically. Post-activation health failure can trigger a rollback to the previous active release.

## Install on Ubuntu

Register the agent in the AIOC Engineering dashboard first and copy the one-time token. Export the values shown in `scripts/install_agent.sh --help`/its usage text, then run the installer with `sudo -E`.

The installer deliberately does **not** create broad sudo rules. If a non-root server agent must restart systemd units or manage Docker/nginx, grant only the exact operating-system permissions needed for those resources.

## Security boundaries

- separate agent token; only its SHA-256 is stored centrally
- organization-scoped tasks and artifacts
- declared capability allow-list
- filesystem allow-list
- executable allow-list for project build/test commands
- no `shell=True`
- artifact SHA-256 verification
- tar traversal and symlink rejection
- service-name validation and optional service allow-list
- health-check host allow-list

## Server operations capabilities

Server nodes may additionally declare `service.status`, `service.logs`, `service.start`, `service.stop`, `service.restart`, `docker.list`, `docker.logs`, `docker.restart`, deployment capabilities, and `publish.nginx`. Service/container controls are default-deny until `AIOC_AGENT_ALLOWED_SERVICES` / `AIOC_AGENT_ALLOWED_CONTAINERS` are explicitly configured. The installer never grants sudo automatically.
