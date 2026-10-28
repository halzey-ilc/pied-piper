# identity (control plane)

Global user accounts and authentication. Control-plane only; tenant access is
granted through `organizations` memberships, never from token claims.

## Capabilities

- **Login / tokens** — `POST /api/v1/auth/token/` and `.../refresh/` (SimpleJWT,
  HS256, rotating refresh + blacklist).
- **Logout** — `POST /auth/logout/` revokes one refresh token (blacklist);
  `POST /auth/logout-all/` revokes every outstanding refresh token for the user.
  Access tokens remain valid until they expire (minutes).
- **Account lockout** — `LoginLockout` counts failed logins per username within a
  sliding window and locks for a fixed duration (`429 account_locked`). A
  successful login clears the counter. Configured via `LOGIN_*`.
- **MFA (TOTP)** — enrol (`/auth/mfa/enroll/`), confirm (`/auth/mfa/confirm/`,
  returns one-time recovery codes), disable (`/auth/mfa/disable/`, needs the
  password). When enabled, `/auth/token/` returns `401 mfa_required` until the
  request carries a valid `otp` (a TOTP code or a recovery code). The TOTP
  algorithm is pure stdlib (`domain/totp.py`); secrets are encrypted at rest
  with Fernet, recovery codes stored as SHA-256 hashes and consumed once.
- **OIDC login** — `POST /auth/oidc/` verifies an IdP ID token against the
  provider JWKS (RS256, issuer/audience/expiry) and returns CRM tokens. Users are
  matched by verified email; `OIDC_AUTO_PROVISION` optionally creates them.
- **Password reset** — `POST /auth/password-reset/` emails a one-time link (same
  response whether or not the email exists); `POST /auth/password-reset/confirm/`
  validates the token, applies the password policy, and clears lockout. Tokens
  are random, stored hashed, single-use and time-limited (`infrastructure/
  password_reset.py`). Delivery uses Django email (console in dev, SMTP in prod).

## Layout

- `domain/totp.py` — RFC 6238 TOTP + recovery-code generation (pure Python).
- `infrastructure/lockout.py`, `infrastructure/mfa.py`, `infrastructure/oidc.py`,
  `infrastructure/password_reset.py` — stateful services (ORM, Fernet, PyJWT,
  email) and the JWKS resolver.
- `infrastructure/authentication.py` — `FallbackJWTAuthentication`, which accepts
  tokens signed by a previous key during signing-key rotation (verification
  only). The `reencrypt_mfa_secrets` management command re-encrypts TOTP secrets
  after an MFA-key rotation. See docs/operations/secret-rotation.md.
- `infrastructure/models.py` — `User`, `LoginState`, `MfaDevice`,
  `MfaRecoveryCode`, `PasswordResetToken`.
- `presentation/` — DRF serializers, views and auth-specific error types.

## Configuration

`LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_MINUTES`, `LOGIN_FAILURE_WINDOW_MINUTES`,
`MFA_ISSUER`, `MFA_ENCRYPTION_KEY`, `OIDC_ENABLED`, `OIDC_ISSUER`,
`OIDC_CLIENT_ID`, `OIDC_JWKS_URI`, `OIDC_AUTO_PROVISION`, `EMAIL_*`,
`DEFAULT_FROM_EMAIL`, `PASSWORD_RESET_TTL_MINUTES`, `PASSWORD_RESET_URL_BASE`,
and the rotation fallbacks `DJANGO_SECRET_KEY_FALLBACKS`,
`JWT_SIGNING_KEY_FALLBACKS`, `MFA_ENCRYPTION_KEY_FALLBACKS` (see `.env.example`).
