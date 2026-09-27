# AIOC Helm deployment

Use an external PostgreSQL database and create `aioc-secrets` with at least `AIOC_DATABASE_URL`, `AIOC_OWNER_TOKEN`, and `AIOC_MASTER_KEY`. Production intentionally disables the legacy shared event-token endpoint; applications should use signed per-application webhooks.
