# Architecture — AIOC 0.8.0-alpha.1 / M4

AIOC memisahkan reasoning AI dari authority dan execution node.

```text
                          USER
                           |
              natural-language command
                           v
                    AIOC CENTRAL
       Planner / Operator / Policy / Audit / Memory
              |                         |
              |                         +--> App connectors / workflows
              |
       outbound-poll task protocol
          /                       \
         v                         v
 LOCAL DEVELOPER AGENT       UBUNTU/SERVER AGENT
 scoped repositories         scoped roots/services/containers
 read/search/patch/test       status/logs/recovery/deploy
 commit/package               stage/activate/health/publish
          \                       /
           +---- verified artifact ----+
                        |
                  release history
                        |
               verify / rollback
```

## Trust boundaries

The central planner never receives an unrestricted shell. A model can only request typed operations. The central service revalidates project/agent/capability ownership after model planning, and the edge agent performs a second local allowlist check.

Local/server nodes poll outward using a node-specific bearer token. This avoids requiring inbound SSH access to a developer laptop and keeps NAT/home-network deployment simpler.

## Engineering flow

`ProjectProfile` binds a project to one source agent and stores repo path, build/test argv and artifact exclusions. `DeploymentTarget` binds the same project to a server agent and defines environment, target root, service type, health endpoint and publishing mode.

A deploy run is a durable state machine:

`source_inspect -> source_prepare -> deploy_stage -> deploy_activate -> deploy_health -> publish? -> complete`

Failures after activation trigger `deploy.rollback` when a previous active release exists.

## Autonomous developer

Developer sessions start only from a clean Git baseline. The AI can request only `read/search/patch/test/finish/fail`. Patches are unified diffs, path-checked locally, and only touched paths are staged. After a patch, a successful test phase is mandatory before completion. Auto-deploy requires auto-commit and is blocked if the working tree still contains uncontrolled changes after commit.

Secret-like files are excluded at tool level, not only by prompt. Symlinks are not followed for context/search/package operations.

## Autonomous server troubleshooting

Server sessions always start with `system.status`, then an AI provider may select only declared capabilities such as `service.status`, `service.logs`, `service.restart`, `docker.list`, `docker.logs`, or `docker.restart`. The loop is iteration-bounded. Missing capability causes `blocked_capability`, not privilege escalation.

Service/container operations are disabled locally until explicit allowlists exist on the server agent.

## Persistence

Schema v12 includes tenant-owned agent nodes/tasks, artifacts, projects, targets, deployment runs/releases, autonomous developer sessions, autonomous server sessions, and operator runs in addition to the M3 operations platform schema.

Logical snapshots auto-include these tables; integrity verification validates agent/project/target/task/session tenant ownership.
