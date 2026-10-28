# Security notes

This document records the controls implemented in the scaffold, the deployment
checks they depend on, and the gaps that remain. It is not a compliance
certificate and makes no claim that any deployment is "100% secure".

## Trust boundaries

- The browser supplies no trusted role, organization, price, approval, task
  status, staff identity, or payment state. Each is loaded or derived on the
  server.
- The public site, client app, staff app, and Django admin share one backend.
  Django admin is a break-glass support surface and is restricted to accounts
  with `is_staff`; application roles still control the staff app.
- Uploaded files and webhook payloads are untrusted input.

## Configuration fail-closed

`config/settings/production.py` collects unsafe conditions and raises
`ImproperlyConfigured` before the app starts. It refuses:

- a missing, short, or development-pattern `DJANGO_SECRET_KEY`
- missing or wildcard `DJANGO_ALLOWED_HOSTS`
- a non-HTTPS `APP_BASE_URL`
- a SQLite database
- disabled SSL redirect or insecure session/CSRF cookies
- HSTS shorter than one year
- the console email backend
- the noop malware scanner
- a process-local cache (rate limits would not be shared)
- payments enabled without both Paystack secrets

Defaults were tightened so a missing `DJANGO_ALLOWED_HOSTS` fails rather than
silently accepting localhost in production.

## Identity and sessions

- Passwords use Django's configured hashers and validators (minimum 12
  characters, common-password and numeric checks). Password history prevents
  immediate reuse.
- Email and password-reset codes are random, HMAC-protected with the server
  secret, single-use, time-limited, attempt-capped, and bound to the user's
  credential version. Failed attempts and supersession are committed even
  though the request fails.
- Login attempts are rate-limited by address and IP, and repeated failures lock
  the account temporarily. Lockouts are audited.
- Sessions are bound to `User.credential_version`; a password change or reset
  invalidates other sessions. Idle expiry is enforced server-side.
- Staff must enrol TOTP before using the staff app. Secrets are encrypted with
  Fernet using a key derived from `DJANGO_SECRET_KEY` and `MFA_ENC_CONTEXT`.
  Accepted timesteps are stored to reject replay.
- Sensitive staff actions require a fresh password (+ TOTP) step-up.
- Registration and password reset respond identically whether or not an account
  exists.

### Key rotation caveat

Because the MFA encryption key is derived from `DJANGO_SECRET_KEY`, rotating the
secret key makes existing TOTP enrolments unreadable. Affected staff must reset
MFA and enrol again. Plan this operation; do not rotate production secrets
without authorization and a rollback procedure.

## Authorization and tenancy

- Client queries are scoped to the active organization membership in the access
  layer, not only in views.
- Staff visibility is permission-based; specialists only see requests with an
  active assignment to them.
- `available_actions()` is a rendering projection. Every mutation re-checks
  authorization in the service layer.
- Cross-tenant task, quote, file, and download access is covered by regression
  tests.

## State integrity

- Legal state changes are declared in `apps/tasks/transitions.py`. Reason
  requirements, restoring a held state, and terminal states are explicit.
- Transitions use a row lock plus a conditional `UPDATE` with the expected
  status, so a stale page cannot overwrite a newer state. Each change writes a
  `TaskStatusEvent` and an `AuditEvent` in the same transaction.
- The `system=True` path is limited to verified provider events; it skips the
  actor permission check but still enforces the table, reasons, preconditions,
  and audit trail.
- Quote acceptance is idempotent per user, scope, and key. A replay returns the
  recorded outcome instead of creating a second invoice.
- Plan allowances are unique per task and kind. Cancelling a covered request
  releases the allowance. Release is blocked unless an allowance, a zero-value
  acceptance, or a paid invoice is on record.

## Payments

- Paystack webhooks are verified with HMAC-SHA512 over the raw body.
- Events are stored once per `(provider, event_id)`. Duplicates are reported as
  replays without reapplying effects.
- Amount and currency must match the stored payment attempt exactly.
- Redirect parameters from the browser are never treated as proof of payment.
- Finance verification requires the exact invoice amount and a unique reference.

## Files

- Uploads are limited by size and extension, sanitised, and checked against
  magic bytes. Content type is derived server-side.
- Files are stored outside the web root under random keys and are never exposed
  through `MEDIA_URL`.
- Downloads require authentication plus either a server-issued, short-lived
  signed token or a fresh authorization check derived from the related task.
  Quarantined or unscanned files are refused.
- Production refuses the noop scanner. `scan_files` also refuses it.

## Logging and audit

- Request IDs are accepted only in a constrained format and echoed back.
- Structured logging redacts values under sensitive-looking keys. Secrets, OTPs,
  tokens, and document contents must never be added to logs in the first place.
- Audit metadata drops sensitive keys and truncates values. Audit rows are
  read-only in Django admin.
- Security headers (CSP, frame denial, referrer policy, permissions policy) are
  applied centrally; authenticated pages are marked `no-store` and `noindex`.

## Deployment checklist

1. Use PostgreSQL with backups and point-in-time recovery. Install
   `requirements/prod.txt`, which pins the `psycopg` driver.
2. Terminate TLS at the proxy and forward `X-Forwarded-Proto`; set
   `SECURE_SSL_REDIRECT=true`, `SECURE_COOKIE=true`, and a one-year HSTS value.
3. Provide a shared cache (Redis) for rate limiting.
4. Configure SMTP or a transactional email provider.
5. Install ClamAV/clamd and set `FILE_SCAN_BACKEND=clamdscan`.
6. Store secrets in the platform secret manager; never in the repository.
7. Run `python manage.py check --deploy --settings=config.settings.production`.
8. Run `python manage.py migrate` with a reviewed migration plan and rollback.
9. Set up `process_outbox` and `scan_files` as scheduled jobs.
10. Forward audit and application logs to a retention-controlled store.
11. Restrict Django admin by network policy and strong admin accounts.

## Verification performed

On 2026-10-28, against Python 3.13:

- `python -m pytest` — 95 passed.
- `python manage.py makemigrations --check --dry-run --settings=config.settings.test`
  — no changes detected.
- `python manage.py check --deploy --settings=config.settings.production` with
  CI-only environment values — one warning, `security.W021` (HSTS preload not
  enabled), deliberately not changed.
- `pip-audit -r requirements/base.txt` and `-r requirements/prod.txt` — no known
  vulnerabilities after updating Django to 5.2.18 and cryptography to 50.0.2.
- `npm audit --audit-level=high` — 0 vulnerabilities.
- `npm run css:build` — succeeded.

The gitleaks job is configured in CI but was not run locally because Docker is
not installed on the development host. It passed on GitHub for commit
`577b434`, along with the test, migration, deploy-check, and dependency-audit
jobs.


## Known gaps and follow-ups

- No Paystack checkout initialization or refund flow; payments are webhook and
  manual-reconciliation only.
- No object-storage adapter; local storage must be backed up and is not suitable
  for multi-instance deployments without shared storage.
- In-app notifications and the messaging/CMS surfaces are not implemented.
- Concurrency guarantees rely on PostgreSQL row locks; SQLite is development
  and test only.
- HSTS preload is intentionally not enabled. It is a long-lived commitment and
  needs an explicit decision.
- A production incident-response runbook, data-retention schedule, and
  independent penetration test are still required before launch.
