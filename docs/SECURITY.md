# Security Model — M4 Engineering Autonomy

## Authority principle

`AI reasoning != production authority`.

Application actions retain `READ -> AUTO -> CONTROLLED -> APPROVAL -> FORBIDDEN`. Engineering/server operations add a second boundary: the model can only select typed capabilities registered centrally **and** enabled locally on the agent.

## Agent identity

- Every local/server agent receives an independent high-entropy token; only SHA-256 is stored centrally.
- Agent tokens are separate from human/API credentials and can be rotated.
- An agent can claim only tasks assigned to its own ID.
- Task lease/retry prevents a crashed node from permanently holding a job.
- Node heartbeat provides online/stale state.

## Local execution safety

- `subprocess(..., shell=False)` only.
- Repo/build/test commands are argv arrays and executable-allowlisted.
- Filesystem paths must stay under `AIOC_AGENT_ALLOWED_ROOTS`.
- Repo traversal, `.git` mutation paths and symlink escape are rejected.
- `.env`, private keys, credential directories/files and similar secret paths are hard-denied from AI read/search/patch/artifact packaging.
- Autonomous commit stages only AI-touched paths, never `git add -A`.
- Dirty baseline blocks autonomous repair. Dirty state after autonomous commit blocks auto-deploy.

## Ubuntu/server safety

- `AIOC_AGENT_ALLOWED_SERVICES` and `AIOC_AGENT_ALLOWED_CONTAINERS` are default-deny. Empty values disable service/container operations.
- The installer never creates sudoers rules or grants root automatically.
- systemd/Docker/nginx OS permissions remain explicit administrator decisions.
- Nginx publishing is disabled unless `AIOC_AGENT_ALLOW_NGINX=true`.
- Health probes are restricted to `AIOC_AGENT_ALLOWED_HEALTH_HOSTS`.
- No arbitrary shell capability exists in the standard agent.

## Deployment safety

- source revision and dirty state recorded before packaging
- build/test steps bounded and predefined by project profile
- SHA-256 artifact verification at upload/download
- safe tar extraction rejects absolute/traversal/symlink escape
- releases staged into versioned paths, then atomically activated
- post-activation health verification mandatory when configured
- previous active release retained for rollback

## Platform identity & tenancy

Bootstrap owner token, scoped API keys and optional OIDC/JWT remain supported. Organization ownership is enforced for agent, project, target, deployment, developer/server session and task records. Integrity checks detect cross-tenant references.

## Production requirements

Use TLS, strong owner/master/agent secrets, PostgreSQL, explicit network allowlists, restricted OS users, audited sudo/polkit rules when needed, firewall, backup/restore drill, and separate staging/production targets. Never expose the agent token or central DB publicly.
