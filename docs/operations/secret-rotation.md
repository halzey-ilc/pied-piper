# Secret rotation runbook

Rotating a secret without downtime means running old and new values in parallel
for a short window, so material signed/encrypted with the old value keeps working
until it ages out. Secrets are delivered as files (`<FIELD>_FILE`, see
[production-hardening](production-hardening.md)); rotation changes those values
and, where overlap is needed, a companion `*_FALLBACKS` value.

General shape: **add new → deploy (old still accepted) → drain/re-encrypt → drop
old → deploy**.

## JWT signing key (`JWT_SIGNING_KEY`)

Tokens signed with the old key must keep verifying until they expire (access:
minutes; refresh: `JWT_REFRESH_TTL_HOURS`).

1. Generate a new key. Set `JWT_SIGNING_KEY=<new>` and
   `JWT_SIGNING_KEY_FALLBACKS=<old>`. Deploy. New tokens use the new key; old
   tokens still verify via the fallback (`FallbackJWTAuthentication`).
2. Wait for the longest token lifetime (the refresh TTL) so every old token has
   expired or been rotated.
3. Remove the fallback (`JWT_SIGNING_KEY_FALLBACKS=`). Deploy. Old tokens are now
   rejected.

## Django secret key (`DJANGO_SECRET_KEY`)

Used for sessions, password-reset tokens and other signed values; Django honours
`SECRET_KEY_FALLBACKS` natively.

1. `DJANGO_SECRET_KEY=<new>`, `DJANGO_SECRET_KEY_FALLBACKS=<old>`. Deploy.
2. Wait for the longest-lived signed value (e.g. `PASSWORD_RESET_TTL_MINUTES`,
   session age).
3. `DJANGO_SECRET_KEY_FALLBACKS=`. Deploy.

## MFA encryption key (`MFA_ENCRYPTION_KEY`)

TOTP secrets are encrypted with Fernet; rotation re-encrypts them.

1. `MFA_ENCRYPTION_KEY=<new>`, `MFA_ENCRYPTION_KEY_FALLBACKS=<old>`. Deploy.
   Existing secrets still decrypt via the fallback; new ones use the new key.
2. Re-encrypt everything under the new key:

   ```bash
   docker compose -f compose.yaml -f compose.prod.yaml run --rm \
     -e DJANGO_SETTINGS_MODULE=config.settings.production api \
     python manage.py reencrypt_mfa_secrets
   ```

3. `MFA_ENCRYPTION_KEY_FALLBACKS=`. Deploy.

Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Database / broker / cache passwords

These need no application-side overlap — the app reads the new value from its
secret file on restart. To avoid a blip, rotate with a brief dual-credential
window at the service:

1. Add the new password at PostgreSQL/RabbitMQ/Redis (a second credential, or a
   short period where both are valid).
2. Update the secret file and restart the app/worker so they reconnect with the
   new value.
3. Remove the old credential at the service.

For PostgreSQL, the application and provisioning roles are separate
(`DB_PROVISION_*`), so they can be rotated independently.

## Webhook / provider secrets

Rotate at the provider, update the corresponding `*_FILE`/env value, and restart.
Where a provider supports two active signing secrets, overlap at the provider;
otherwise schedule a brief maintenance window.

## After any rotation

- Confirm the old value is fully removed (no stale `*_FALLBACKS`, no old file).
- Confirm `pip-audit`/monitoring show no regressions and that logins, refresh and
  (if rotated) MFA still work.
