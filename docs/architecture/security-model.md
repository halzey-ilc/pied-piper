# Security model

## What is implemented

- **Authentication:** JWT (SimpleJWT, HS256) with a signing key separate from
  `DJANGO_SECRET_KEY`, short access tokens, rotating refresh tokens with
  blacklist, issuer/audience claims. Token claims carry no tenant or database
  information.
- **Password storage:** Argon2 (`argon2-cffi`) as the default hasher; minimum
  length and common-password validators. No custom cryptography anywhere —
  signatures use the standard-library `hmac`.
- **Authorization:** RBAC with own/team/all ownership scopes
  (`modules.access_control`), enforced inside use cases on every operation, not
  only in views.
- **Tenant isolation:** database-per-tenant + fail-closed router + membership
  gate; see [multi-tenancy](multi-tenancy.md). Proven in
  `tests/security/test_tenant_isolation.py`.
- **Transport/headers:** production enables HSTS, secure cookies, SSL redirect
  (health checks exempt). `nosniff`, `DENY` framing, same-origin referrer and
  COOP always on. CORS is restricted to explicit origins over the `/api/` path
  only; credentials disabled.
- **Rate limiting:** DRF throttles — anon/user defaults, a stricter `auth`
  scope on token endpoints, and a `webhooks` scope.
- **Input limits:** `DATA_UPLOAD_MAX_MEMORY_SIZE` and a per-integration webhook
  body cap.
- **Configuration validation:** `config/settings/env.py` validates every
  variable with Pydantic and refuses to start on unsafe production settings
  (DEBUG on, placeholder/short secrets, reused secret, wildcard hosts, non-TLS
  database, missing Redis password or metrics token, diagnostic tasks enabled).
  Error messages name the offending variable but never print its value.
- **Secret hygiene:** no secrets in git; `.env` is git- and docker-ignored;
  `.env.example` carries only empty or `__GENERATE__` placeholders
  (`tests/security/test_secrets_hygiene.py`). `scripts/init_env.sh` generates
  secrets without echoing them.
- **Error handling:** uniform JSON errors; unexpected exceptions return a
  generic 500 with no stack trace or exception text; the detail is logged
  server-side with redaction.
- **Logging:** structured JSON with request/correlation/tenant context; a
  redaction filter removes passwords, tokens, authorization headers, URL
  credentials and customer message content/audio from logs.
- **Webhooks:** provider signatures verified (HMAC-SHA256 / shared-secret /
  timestamped HMAC with replay window) before any storage; duplicates dropped
  via the inbox unique constraint.
- **Audit:** append-only records (application-enforced; DB-level enforcement is
  a planned step below).

## Threats explicitly addressed

- Cross-tenant data access → layered isolation, 404 (no enumeration).
- Prompt/field injection into logs → id sanitisation + redaction filter.
- Webhook spoofing / replay → signature + timestamp window + dedup.
- Secret leakage via errors/logs → redaction, generic 500, no secrets in env
  error messages.
- SQL injection on tenant DB names → strict validation + identifier quoting.
- Retry storms → exponential backoff with full jitter.

## NOT done — required before any production use

This scaffold is **not production-ready**. Outstanding hardening:

- Real secret management (Vault/KMS/SOPS) instead of a `.env` file; secret
  rotation.
- TLS everywhere (PostgreSQL `verify-full`, Redis, RabbitMQ, ingress) with
  managed certificates.
- A dedicated least-privilege application DB role, separate from the
  CREATEDB-capable provisioning role; per-database GRANT hardening.
- Database-level audit immutability (REVOKE UPDATE/DELETE, append triggers).
- OAuth 2.0 / OIDC, MFA, account lockout and password-reset flows.
- WAF / ingress rate limiting; DDoS protection; security headers at the edge.
- Dependency and image scanning in CI with enforced thresholds; SBOM.
- Backup **and tested restore** (see [backup-restore](../operations/backup-restore.md)),
  DR runbook, failover.
- Penetration test and threat-model review.
