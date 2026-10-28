# Security model

## What is implemented

- **Authentication:** JWT (SimpleJWT, HS256) with a signing key separate from
  `DJANGO_SECRET_KEY`, short access tokens, rotating refresh tokens with
  blacklist, issuer/audience claims. Token claims carry no tenant or database
  information.
- **Account lockout:** repeated failed logins per username are counted within a
  sliding window and lock the account for a fixed duration (`429 account_locked`);
  a successful login clears the counter (`modules.identity`, configured via
  `LOGIN_*`).
- **Multi-factor authentication:** optional per-user TOTP (RFC 6238, stdlib
  `hmac`), enrolled and confirmed via the API, enforced at login (`401
  mfa_required` then an `otp` field). Secrets are encrypted at rest (Fernet) and
  single-use recovery codes are stored hashed.
- **External login (OIDC):** optional OpenID Connect relying party verifies an
  IdP-issued ID token against the provider's JWKS (RS256, issuer/audience/expiry
  checked) and exchanges it for CRM tokens; accounts are matched by verified
  email (auto-provisioned only when enabled).
- **Password storage:** Argon2 (`argon2-cffi`) as the default hasher; minimum
  length and common-password validators. TOTP and OIDC use vetted primitives
  (`hmac`, `PyJWT`/`cryptography`); no bespoke crypto.
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
- **Audit:** append-only records, enforced both in the application (the model
  refuses update/delete) and in the database (a `BEFORE UPDATE OR DELETE` trigger,
  migration `audit/0002_audit_append_only`), so the guarantee survives an ORM
  bypass.
- **Secret delivery:** every secret can be supplied as a mounted file via a
  `<FIELD>_FILE` variable (`config/settings/env.py`), so a secret manager
  (Docker/K8s secrets, Vault Agent, SOPS) can inject it without the plaintext
  ever entering the process environment. `compose.prod.yaml` wires this.
- **Least-privilege DB role:** provisioning (CREATEDB) uses a role distinct from
  the request-handling role when `DB_PROVISION_USER`/`_PASSWORD` are set
  (`infrastructure/postgres/prod-least-privilege-roles.sql.example`).
- **Transport security:** opt-in TLS for PostgreSQL (`verify-full`), Redis
  (`rediss` + CA) and RabbitMQ (`amqps` + CA), plus an nginx TLS edge; see the
  production-hardening runbook below.

## Threats explicitly addressed

- Cross-tenant data access → layered isolation, 404 (no enumeration).
- Prompt/field injection into logs → id sanitisation + redaction filter.
- Webhook spoofing / replay → signature + timestamp window + dedup.
- Secret leakage via errors/logs → redaction, generic 500, no secrets in env
  error messages.
- SQL injection on tenant DB names → strict validation + identifier quoting.
- Retry storms → exponential backoff with full jitter.

## Hardening status

The code and configuration for the items below live in the repository; the
**runbook** that ties them together, including what you must supply (certs,
secrets, domains), is [production-hardening](../operations/production-hardening.md).

Implemented in code/config (needs your infrastructure to activate):

- Secret delivery via files (`<FIELD>_FILE`), ready for Vault/KMS/SOPS/Docker
  secrets; wired in `compose.prod.yaml`.
- TLS for PostgreSQL (`verify-full`), Redis (`rediss`), RabbitMQ (`amqps`) and an
  nginx TLS edge with HSTS and coarse rate limiting.
- Least-privilege application DB role separate from the CREATEDB provisioning
  role; per-database GRANT hardening script.
- Database-level audit immutability (append-only trigger + REVOKE in the role
  script).
- PgBouncer transaction pooling in front of PostgreSQL.
- Dependency and image scanning in CI with enforced HIGH/CRITICAL thresholds
  (Trivy) and a published CycloneDX SBOM.
- Edge security headers and request rate limiting (nginx).

- Account lockout, TOTP MFA (with recovery codes) and an OIDC relying party for
  external login (`modules.identity`).

Still outstanding — needs product/ops decisions or external services:

- Secret **rotation** automation (the delivery mechanism is in place).
- Self-service password-reset and an OIDC/OAuth logout/session-revocation flow.
- A managed WAF / DDoS protection in front of the nginx edge; managed TLS certs.
- Backup **and tested restore** (see [backup-restore](../operations/backup-restore.md)),
  DR runbook, failover.
- Penetration test and threat-model review.
