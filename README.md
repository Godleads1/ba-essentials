# B & A Essentials

Managed-service platform for Nigerian SMEs: a public site, a client workspace,
and a staff operations app on one Django backend. This repository implements
Phases 1–4 of the implementation plan: secure foundations, identity and access,
transactional domain data, and the request-to-acceptance lifecycle.

## Stack

- Python 3.13 / Django 5.2 LTS
- Server-rendered templates + HTMX (vendored)
- Tailwind CSS v4 CLI (compiled to `static/css/app.css`)
- SQLite locally and in tests; PostgreSQL in production
- pytest + pytest-django; pip-audit and gitleaks in CI

## Layout

```
manage.py
config/            settings (local, test, production), URLs, env/logging helpers
apps/
  accounts/        users, staff roles, OTP, MFA, sessions, step-up auth
  organizations/   tenants, memberships, invitations
  catalogue/       versioned service catalogue
  files/           private storage, validation, scanning, signed downloads
  tasks/           requests, assignments, transition matrix, work logs
  quotes/          versioned quotes, acceptance, rejection
  reviews/         deliverables, independent QA, release, client acceptance
  billing/         plans, allowances, invoices, Paystack webhook
  operations/      staff workspace views
  audit/           append-only audit events
  core/            middleware, idempotency, rate limits, outbox, sequences
templates/         base shells, auth, client app, staff app, public, errors
static/            Tailwind source, compiled CSS, htmx
tests/             settings, auth, transitions, billing, files, isolation, views
docs/              security notes and deployment guidance
```

## Local setup

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash
pip install -r requirements/dev.txt
npm install
npm run css:build

cp .env.example .env
python manage.py migrate
python manage.py create_staff you@example.test --role admin --admin
python manage.py runserver
```

`create_staff` prompts for a password and marks the address verified. Staff
accounts must enrol TOTP two-factor authentication at first login; the
middleware enforces this server-side.

For a disposable demo database:

```bash
python manage.py seed_demo --yes
```

This refuses to run unless `DEBUG` is enabled and prints one generated password
for all synthetic `@example.test` accounts.

### Catalogue data

Pricing and service offerings need founder approval, so no real catalogue is
committed. Load an approved manifest:

```bash
python manage.py bootstrap_catalogue docs/catalogue.example.json
```

Re-running is safe; changed scope or deliverables publishes a new version and
keeps the old one for existing requests.

## Tests

```bash
python -m pytest
python manage.py makemigrations --check --dry-run --settings=config.settings.test
```

The suite covers production fail-closed settings, OTP attempt limits and
invalidation, lockout and rate limits, MFA replay, session credential binding,
tenant isolation, transition legality and stale writes, quote idempotency,
allowance versus invoice, webhook signatures and duplicate events, release
payment gating, QA independence, file magic-byte checks and signed downloads,
a full lifecycle, and page rendering for each surface.

CI runs the tests, the migration check, a production `check --deploy`, both
dependency audits, a secret scan, and verifies that compiled CSS is current.
The production deploy check needs the PostgreSQL driver from
`requirements/prod.txt` plus the production environment variables; the workflow
supplies inert values for the check.

## Security posture

Implemented controls and deployment requirements are documented in
[docs/SECURITY.md](docs/SECURITY.md). Highlights:

- Production settings refuse to start with unsafe secrets, wildcard hosts,
  SQLite, missing TLS/HSTS, console email, a noop malware scanner, a local cache,
  or payments enabled without Paystack secrets.
- Every tenant query is scoped in the service or access layer; hiding UI is
  never treated as authorization.
- Credential versions invalidate sessions after password changes; staff MFA and
  step-up checks are enforced server-side.
- State changes go through an explicit transition table with conditional
  updates, reason requirements, and audit records in the same transaction.
- Files are stored outside the web root, validated by magic bytes, quarantined
  until clean, and served only through authorized downloads.
- Payment state is driven by signed, deduplicated webhooks or finance
  verification with exact amount and currency checks.

## Known limitations (deliberate)

- Paystack checkout initialization is not implemented. Only verified webhooks
  and manual finance reconciliation change payment state.
- Object storage (for example Cloudflare R2) is not implemented; the local
  adapter refuses unknown backends instead of silently falling back.
- In-app messaging and the website CMS are Phase 5+ and out of scope here.
- SQLite cannot exercise real row-lock races; PostgreSQL is required in
  production and concurrency tests should run against it.
- Notification outbox handling exists, but in-app notification delivery is not
  implemented yet.

This project does not claim compliance certification or that any deployment is
"100% secure".
