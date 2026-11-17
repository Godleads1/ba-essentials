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
- Django admin requires the same enrolled second factor as the staff app, for
  every `is_staff` account with or without a staff role. Its built-in sign-in
  form is replaced by a redirect to the app's sign-in, so admin access always
  goes through the app's rate limits, lockout and TOTP step; the redirect never
  reads a submitted password and keeps only `/admin/` destinations
  (`tests/test_admin_access.py`).
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

- **Clients have no password.** They sign in with a 6-digit code sent by
  WhatsApp, SMS (Termii) or email (`apps/accounts/codes.py`). Existing client
  passwords were made unusable by migration `accounts.0002`. Staff and Django
  admins sign in at `/login/staff/` with password + TOTP; the client sign-in
  never sends them a code, and the password sign-in refuses client accounts.
- Sign-in codes use the same challenge machinery as below (HMAC, single use,
  bound to account and purpose, expiry, a new code cancels the old one). Sends
  are limited per phone number/email (5 an hour) and per IP (20 an hour); code
  checks per IP (30 per 10 minutes); five wrong codes lock the account, and
  each lockout in a row lasts twice as long (capped at a day).
- Requests for a code answer the same way whether or not an account exists:
  same next page, same wording, a minimum response time
  (`AUTH_MIN_RESPONSE_SECONDS`), and Termii failures are not shown.
- Phone numbers are stored in E.164. A number becomes a way to sign in only
  after a code sent to it has been entered; confirming it removes it from any
  account that typed it without proving it, and a database constraint stops
  two accounts holding the same confirmed number. Sign-up always sends the
  first code to the email address; unproven sign-up details are replaced by
  the next sign-up for that email.
- Clients confirm sensitive actions (step-up) with a fresh code, plus their
  authenticator code if they set one up.
- Changing the email or phone number needs a fresh step-up, then a code sent
  to the **new** address or number; the old one gets a notice (email, and SMS
  for a confirmed old number). A detail another account already uses gets the
  same answer and no code. Each change bumps the credential version, so other
  devices are signed out and outstanding codes stop working. Removing the
  phone and turning the client authenticator on or off also send a notice;
  turning it off needs a code from the app. Staff can't turn theirs off.
- "Sign out" on a device, and "Sign out of all other devices", delete the
  server-side session, not just the list entry; you can only end your own.
- "Keep me signed in" (clients only): by default up to 30 days, ending after
  7 days without use. These are defaults, not enforced caps: operators can
  change them with `REMEMBER_ME_SECONDS` and `REMEMBER_ME_IDLE_TIMEOUT`.
- Codes are never logged, put in URLs or kept in plain text; code records are
  deleted after a day by `process_outbox` (Privacy Policy).
- Passwords (staff) use Django's configured hashers and validators (minimum 12
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
- Staff see a client's request (brief, messages, files) only on a need-to-know
  basis (`apps/tasks/access.py`): operations, quote approvers and administrators
  see all; specialists their active assignments; reviewers requests in review;
  support requests from businesses whose open tickets they handle (access ends
  when the ticket is resolved); finance only a request's invoices, fees and fee
  evidence, never its contents. Deactivated staff see nothing.
- Every staff look at client data is in the audit log: `staff.task_opened` (with
  the client organisation and whether it was the full or money-only view) and
  `staff.list_viewed` for queues, lists, searches, the finance page, support
  tickets and the audit log itself. Search words are not stored.
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
- Finance verification serialises on a conditional invoice update, so two
  concurrent confirmations cannot both record a successful payment.
- Redirect parameters from the browser are never treated as proof of payment.
- Plan checkout is started on the server (`start_plan_checkout`): only the
  business owner can start it, the price and currency come from the `Plan`
  row, and the customer is the signed-in owner. The hosted checkout address
  must be `https://checkout.paystack.com`.
- Before any `charge.success` is applied, the server calls Paystack's
  verify-transaction API (`apps/billing/paystack.py`) and requires
  `status=success` and the same reference, amount and currency as the stored
  payment attempt. If Paystack can't be reached, nothing is stored and the
  webhook answers 503 so Paystack retries.
- A plan starts only when its invoice moves from issued to paid, so repeated
  or extra events never extend it twice. Paying during an active period
  extends from the current end date. The return page only shows status.
- Tests never call the real Paystack API (`fake_paystack` fixture in
  `tests/conftest.py`).
- Finance verification requires the exact invoice amount and a unique reference.
- Businesses outside Nigeria pay in US dollars through Flutterwave. Its webhook
  must carry the dashboard's secret hash (`verif-hash`, compared in constant
  time), each event is stored once, and Flutterwave's own verify answer must
  match the attempt's provider, amount and currency before anything is paid.
- Prices and currency come from the business's country, set at sign-up and
  changeable only by staff in admin (audited). The browser never chooses them.
  Rest-of-Africa prices use Flutterwave's rate, cached for an hour and bounded;
  if it can't be fetched and no admin fallback is set, dollar checkout stops.
  The rate used is stored on the invoice.
- The yearly discount and extra legal review fees are editable only by super
  admins, in one settings row, and every change is audited with old and new values.
- Each request records the country the work is for (fixed by the service when
  it has one). On a naira or Africa plan, work for a country outside Africa is
  never covered by the allowance; it is quoted, or staff move the business to the
  international plan in admin (audited). Staff with `task.scope` must confirm the
  country before a quote can be sent; a change needs a reason and is audited.
- A confirmed payment from a card issued in another country than the business's
  creates an account flag for staff review. Flags never block the client.
- Request invoices (quotes and third-party fees) are paid through the same
  server-started Paystack checkout as plans. The browser names the invoice;
  the amount always comes from the stored invoice. Only the business owner
  can pay.
- Third-party fees need evidence of the official amount per line and a second
  staff member's approval before the client sees them; the preparer can't
  approve their own. Paystack's local charge is added on top and the total is
  fixed at approval. Fee invoices never count as payment for the work, and a
  request can't be released while fees are open. Paystack's actual fee is
  recorded from its own verification, so a foreign-card shortfall is visible
  to finance.
- Manual renewal (Terms 5.5–5.8): plans never renew by themselves and nothing
  is charged automatically. No card permission is asked for or saved at
  checkout, and the code that charged saved cards has been removed. Cards saved
  before this change are never charged; the owner can remove them.
- `run_renewals` (hourly) issues the renewal invoice and reminds the owners 3
  days before a monthly or yearly period ends, with a pay link. Only a
  confirmed webhook renews anything; paying early carries on from the end of
  the current period. If the period ends unpaid, service continues for 5 business days
  (notices at the start and on the last day), then the account is paused.
- A paused account is read-only, enforced on the server in `client_required`
  (`apps.organizations.access.PAUSED_ALLOWED`): every POST is refused except
  paying, account security and data-rights actions, closing the account,
  switching business and support requests. Staff can't move existing requests
  forward (Terms 5.6.4). Thirty days after pausing, the plan ends and the unpaid
  renewal invoice is voided: not renewing is not a debt (5.6.6). Saying "I won't
  renew" stops reminders and keeps access to the end of the paid period; a
  downgrade starts at the next renewal. Only the owner can renew, cancel,
  downgrade or remove a saved card, and each is audited.

## Files

- Uploads are limited by size and extension, sanitised, and checked against
  magic bytes. Content type is derived server-side.
- Files are stored outside the web root under random keys and are never exposed
  through `MEDIA_URL`.
- Downloads require authentication plus either a server-issued, short-lived
  signed token or a fresh authorization check derived from the related task.
  Quarantined or unscanned files are refused.
- Production refuses the noop scanner. `scan_files` also refuses it.

## Messaging, support and public website

- Task threads are tenant-scoped through the task access layer. Internal notes
  are removed at queryset level for client callers, and clients cannot create
  them; posting attempts are rate-limited.
- Notifications are database rows naming a task reference or ticket subject,
  never message content. Recipients are derived server-side: the client
  organization, the assigned specialist, or the operations/support roles.
- Staff replies and ticket assignment require `support.handle`. Clients can
  only add public replies; closed tickets refuse client replies and new public
  staff replies.
- The public contact form is rate-limited per source address, includes a
  honeypot, and stores only a keyed HMAC of the address using
  `django.utils.crypto.salted_hmac`. Message content is never written to audit
  metadata.
- Website pages keep immutable revisions. Editors with `website.edit` save
  drafts and preview them through an authenticated, permission-gated view;
  `website.publish` controls what the public renderer serves. Publishing,
  unpublishing and restores are audited.
- Revision bodies render as escaped plain text with preserved line breaks; raw
  HTML in a revision is never trusted. Reserved slugs cannot shadow the auth,
  staff, admin, health or contact routes.

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
   `requirements/prod.txt` with `--require-hashes` (every package pinned and
   hash-checked, including the `psycopg` driver).
2. Terminate TLS at the proxy and forward `X-Forwarded-Proto`; set
   `SECURE_SSL_REDIRECT=true`, `SECURE_COOKIE=true`, and a one-year HSTS value.
3. Provide a shared cache (Redis) for rate limiting.
4. Configure SMTP or a transactional email provider.
5. Install ClamAV/clamd and set `FILE_SCAN_BACKEND=clamdscan`.
6. Store secrets in the platform secret manager; never in the repository.
7. Run `python manage.py check --deploy --settings=config.settings.production`.
8. Run `python manage.py migrate` with a reviewed migration plan and rollback.
9. Set up `process_outbox`, `run_renewals`, `run_retention` (daily) and `scan_files` as scheduled jobs. The Render
   staging Blueprint schedules outbox delivery; `scan_files` needs shared
   storage before it can run in a separate container (see
   [DEPLOYMENT.md](DEPLOYMENT.md)).
10. Forward audit and application logs to a retention-controlled store.
11. Restrict Django admin by network policy and strong admin accounts.

The staging Blueprint and its verification steps are in
[DEPLOYMENT.md](DEPLOYMENT.md). It is not a production approval.

## Verification performed

On 2026-10-28, against Python 3.13:

- `python -m pytest` — 131 passed, 5 PostgreSQL-only tests skipped.
- `python -m pytest --ds=config.settings.test_postgres` in CI — 106 passed at
  commit `671b5e6`, including concurrent transitions, quote acceptance,
  idempotent replay, allowance limits, and payment verification. The
  communications, staff-queue and website suites run on PostgreSQL on the next
  push.
- The CI container job builds the production image and runs
  `check --deploy` with `config.settings.staging` inside it.
- `python manage.py makemigrations --check --dry-run --settings=config.settings.test`
  — no changes detected.
- `python manage.py check --deploy --settings=config.settings.production` with
  CI-only environment values — one warning, `security.W021` (HSTS preload not
  enabled), deliberately not changed.
- `pip-audit -r requirements/base.txt` and `-r requirements/prod.txt` — no known
  vulnerabilities after updating Django to 5.2.18 and cryptography to 50.0.2.
- `npm audit --audit-level=high` in `tools/banda-css` (build-time only; nothing
  from npm is served).

The gitleaks job is configured in CI but was not run locally because Docker is
not installed on the development host. It passes on GitHub for each push,
including commit `8361136`, along with the SQLite and PostgreSQL test jobs,
migration check, deploy check, and dependency audit.


## Known gaps and follow-ups

- Refunds and chargebacks are handled by finance by hand (`refund_due`
  payments); there is no in-app refund flow yet.
- No object-storage adapter; local storage must be backed up and is not suitable
  for multi-instance deployments without shared storage.
- In-app messaging, support tickets, staff queues and the text CMS are
  implemented. Outbound message email, a CMS media library, and
  object-storage-backed uploads are not; public contact enquiries live in-app
  only until an email provider is wired.
- Concurrency guarantees rely on PostgreSQL row locks and conditional writes;
  SQLite remains development-only, while CI runs the full suite on PostgreSQL.
- HSTS preload is intentionally not enabled. It is a long-lived commitment and
  needs an explicit decision.
- An independent penetration test is still required before launch. The
  incident and data breach response plan is in
  [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) (draft for legal review), and
  data retention runs daily with `manage.py run_retention`.

## Audit fixes (first security audit, 17 Nov 2026)

- Redirects after sign-in, staff sign-in and "confirm it's you" accept only
  addresses on this site; a `next` kept in the session is checked as well
  (`apps.core.middleware._safe_next`).
- Rate limits use `apps.core.client_ip.client_ip`: `X-Forwarded-For` is trusted
  only for the entries added by our own proxies (`TRUSTED_PROXY_COUNT`), or an
  edge header the proxy overwrites (`CLIENT_IP_HEADER`). `check --deploy` warns
  (`banda.W001`) when neither is set.
- Regression tests: `tests/test_security_audit_fixes.py`.
- Security alerts (`apps/audit/alerts.py`), emailed through the outbox to
  `SECURITY_ALERT_EMAILS` and throttled to one an hour per rule and account:
  a staff account locked out or turning off its second factor; 5 wrong
  authenticator codes in 15 minutes; 3 refused file downloads in an hour;
  any change to staff roles or admin access (`apps/audit/signals.py`, however
  it is made); a payment notification with the wrong amount; 5 with a bad
  signature or unknown reference in an hour. `check --deploy` warns
  (`banda.W002`) when no recipient is set. Tests: `tests/test_security_alerts.py`.
- Private files in Cloudflare R2 (`apps/files/storage.py`, `PRIVATE_STORAGE_BACKEND=r2`):
  a private bucket, server-made keys checked against `..`, absolute paths and
  backslashes, downloads only through the authorised view, and a temporary
  copy for the scanner that is deleted after use. Tests: `tests/test_r2_storage.py`.
