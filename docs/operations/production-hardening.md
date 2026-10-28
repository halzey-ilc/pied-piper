# Production hardening runbook

This describes how to run the stack hardened for production using
`compose.prod.yaml`. The application code and configuration are in the
repository; **you** provide the secrets, TLS certificates and domain. Nothing
secret is committed.

```bash
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

## 1. Secrets (never in the environment)

Each secret is mounted as a file; the application reads `<FIELD>_FILE` and loads
the value from that path (`config/settings/env.py`, `secret_file_overrides`). An
explicit `<FIELD>` value always wins over the file, so local development is
unchanged.

Create the files the overlay expects under `./secrets/` (or point a secret
manager at these paths — Docker/K8s secrets, Vault Agent, SOPS):

```bash
mkdir -p secrets
for name in django_secret_key jwt_signing_key db_password \
            db_provision_password rabbitmq_password redis_password metrics_token; do
  openssl rand -base64 48 | tr -d '\n' > "secrets/$name"
done
chmod 600 secrets/*
```

`DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY` must differ and be ≥ 50 characters;
`config.settings.production` refuses to start otherwise (fail fast, no value
echoed). Rotate by replacing the file and restarting the service; automate
rotation with your secret manager (not yet scripted here).

## 2. TLS certificates

TLS is opt-in and driven by configuration, so the dev stack stays plaintext.
Provide a CA and per-service server certificates under `infrastructure/tls/`:

```
infrastructure/tls/
  ca.crt                     # CA bundle the app verifies against (also mounted into app)
  postgres/{server.crt,server.key,ca.crt,pg_hba.conf}
  redis/{server.crt,server.key,ca.crt}
  rabbitmq/{server.crt,server.key,ca.crt}
  pgbouncer/{server.crt,server.key,ca.crt}
  nginx/{server.crt,server.key}
```

Copy `infrastructure/tls/postgres/pg_hba.conf.example` to `pg_hba.conf` — it
permits only `hostssl`, so any non-TLS connection is refused. Server key files
must be `chmod 600` and owned appropriately for each image.

For a quick internal CA (NOT for public endpoints — use a managed/public CA
there), generate a CA and sign each service cert with SANs matching the compose
service name (`postgres`, `redis`, `rabbitmq`, `pgbouncer`, `api`) and your
public domain for nginx. Any standard tool works (`cfssl`, `mkcert`, `openssl`,
`step-ca`).

How each hop is secured:

| Hop | Mechanism | Settings |
|-----|-----------|----------|
| app → PostgreSQL (via PgBouncer) | `sslmode=verify-full`, CA at `PGSSLROOTCERT` | `DB_SSLMODE`, `DB_USE_PGBOUNCER` |
| PgBouncer → PostgreSQL | `verify-full` | pgbouncer env |
| app → Redis | `rediss://` + CA | `REDIS_USE_TLS`, `REDIS_CA_CERT` |
| app → RabbitMQ | `amqps://` + CA | `RABBITMQ_USE_TLS`, `RABBITMQ_CA_CERT`, `RABBITMQ_PORT=5671` |
| client → nginx | TLS 1.2/1.3, HSTS | `infrastructure/nginx/nginx.conf` |

`verify-full` checks the certificate hostname, so server certs **must** carry
the right SAN. The app trusts X-Forwarded-Proto from the single nginx hop
(`SECURE_PROXY_SSL_HEADER_ENABLED=1`, `TRUSTED_PROXY_COUNT=1`).

## 3. Least-privilege database roles

Development uses one role (`crm_app`) with CREATEDB for convenience. In
production, apply `infrastructure/postgres/prod-least-privilege-roles.sql.example`
once per database as a superuser (substituting real passwords). It creates:

- `crm_provision` — CREATEDB, used only by the provisioning job
  (`DB_PROVISION_USER` / `DB_PROVISION_PASSWORD`). The tenant provisioner
  (`PostgresDatabaseProvisioner`) uses this role when configured.
- `crm_app` — the request-handling role: data DML only, no CREATEDB, and no
  UPDATE/DELETE/TRUNCATE on `audit_record`.
- `crm_migrator` — owns schema objects / runs migrations (DDL).

Run migrations as a controlled step, not on every boot (the overlay sets
`RUN_MIGRATIONS_ON_START=0`):

```bash
docker compose -f compose.yaml -f compose.prod.yaml run --rm \
  -e DJANGO_SETTINGS_MODULE=config.settings.production api \
  python manage.py migrate
```

## 4. Immutable audit log

Migration `audit/0002_audit_append_only` installs a trigger that rejects UPDATE
and DELETE on `audit_record` in every database (control plane and each tenant),
regardless of role. The role script additionally REVOKEs UPDATE/DELETE/TRUNCATE
from `crm_app` (defence in depth; TRUNCATE is not caught by row triggers).
Verified by `tests/integration/test_audit_immutability.py`.

## 5. Edge, pooling, diagnostics

- **nginx** is the only published service (443). It terminates TLS, sets HSTS and
  security headers, applies coarse per-IP rate limiting, and blocks `/metrics` at
  the edge. Put a managed WAF/CDN in front of it for DDoS protection.
- **PgBouncer** (transaction pooling) sits between the app and PostgreSQL;
  `DISABLE_SERVER_SIDE_CURSORS` is already handled via `DB_USE_PGBOUNCER`.
- **Diagnostics** tasks are forced off; `config.settings.env` also refuses
  `DIAGNOSTIC_TASKS_ENABLED=true` in production.

## 6. CI supply-chain checks

`.github/workflows/ci.yml` builds the image and runs Trivy against it, failing on
fixable HIGH/CRITICAL OS/library vulnerabilities, and publishes a CycloneDX SBOM
(`sbom` artifact). `bandit` and `pip-audit` already gate every run.

## Still your responsibility

Secret rotation automation, OAuth2/OIDC + MFA + account lockout, a managed
WAF/CDN and public TLS certificates, backup **with tested restore** and a DR
runbook, and a third-party penetration test. See the "Still outstanding" list in
[security-model](../architecture/security-model.md).
