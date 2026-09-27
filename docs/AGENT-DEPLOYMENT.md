# Local Developer Agent & Ubuntu Server Agent

## 1. Local developer node

Recommended capabilities:

```text
system.status
repo.inspect
repo.context
repo.read
repo.search
repo.apply_patch
repo.run_steps
repo.commit
repo.prepare_release
```

Set `AIOC_AGENT_ALLOWED_ROOTS` to the smallest parent directory containing the repositories AIOC may control. Do not use `/` or your whole home directory when a narrower directory is possible.

## 2. Ubuntu server node

Typical deployment capabilities:

```text
system.status
deploy.stage
deploy.activate
deploy.health
deploy.rollback
service.status
service.logs
service.restart
```

Optional, only when required:

```text
service.start
service.stop
docker.list
docker.logs
docker.restart
publish.nginx
```

Configure roots/services/containers explicitly. The AIOC installer does not grant OS privileges, so systemd/Docker/nginx permissions must be provisioned by the server administrator with least privilege.

## 3. Project profile

A project registers source agent, repo path, build commands, test commands and artifact excludes. Commands are argv arrays; shell strings/pipelines are intentionally unsupported.

## 4. Deployment target

A target registers server agent, environment, target root, optional systemd/docker-compose service, health URL and optional Nginx publishing metadata.

## 5. Operational behavior

Agents make outbound HTTP(S) requests to central AIOC, heartbeat periodically, claim one scoped task, execute it locally, and return structured evidence. They do not require central AIOC to open an SSH connection into the node.
