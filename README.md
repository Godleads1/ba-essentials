# B & A Essentials

Managed-service platform for Nigerian SMEs: a public site, a client workspace,
and a staff operations app on one Django backend. This repository implements
Phases 1–4 of the implementation plan: secure foundations, identity and access,
transactional domain data, and the request-to-acceptance lifecycle.

## Stack

- Python 3.13 / Django 5.2 LTS
- Server-rendered templates + HTMX (vendored)
- The banda design: prebuilt `static/banda/app.css` from the design preview, plus
  `static/banda/staff.css` for the staff pages (Tailwind 3 in `tools/banda-css`)
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
static/            banda stylesheets, fonts and scripts; htmx
tests/             settings, auth, transitions, billing, files, isolation, views
docs/              security notes and deployment guidance
```

## Local setup

**On Windows?** Follow [docs/RUN_LOCALLY.md](docs/RUN_LOCALLY.md): a step-by-step
guide with the two-window setup, demo data, how clients and staff sign in, and
what to do when something goes wrong.

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash
pip install --require-hashes -r requirements/dev.txt

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
ruff check .
python manage.py makemigrations --check --dry-run --settings=config.settings.test
```

The row-lock race tests need a real PostgreSQL server and skip on SQLite:

```bash
DATABASE_URL=postgres://ba:ba@127.0.0.1:5432/ba \
  python -m pytest --ds=config.settings.test_postgres
```

The suite covers production fail-closed settings, OTP attempt limits and
invalidation, lockout and rate limits, MFA replay, session credential binding,
tenant isolation, transition legality and stale writes, quote idempotency,
allowance versus invoice, webhook signatures and duplicate events, release
payment gating, QA independence, file magic-byte checks and signed downloads,
a full lifecycle, and page rendering for each surface.

CI runs the linter (ruff), the tests on SQLite and PostgreSQL, the migration check, a production
`check --deploy`, both dependency audits, a secret scan, and verifies that
compiled CSS is current. The PostgreSQL job exercises concurrent transitions,
quote acceptance, idempotent replay, allowance limits, and payment
verification. The production deploy check needs the PostgreSQL driver from
`requirements/prod.txt` plus the production environment variables; the workflow
supplies inert values for the check.

### Python packages

`requirements/*.in` list what the app needs; `requirements/*.txt` (made by
pip-tools) pin every package, including the ones those pull in, with SHA-256
hashes. Installs use `--require-hashes`, so a changed or swapped package fails
instead of installing. Edit only the `.in` files, then regenerate the `.txt`
files (CI checks they match):

```bash
cd requirements
pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url -o prod.txt prod.in
pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url -o dev.txt dev.in prod.in
```

## Deployment

A Render Blueprint with PostgreSQL, Render Key Value (Redis), Resend SMTP, and
a private ClamAV service is documented in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). It is a staging runbook: paid
resources, synthetic data, and payments disabled.

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

- Paystack plan checkout is implemented; a plan starts only after a signed
  webhook that Paystack's verify API confirms. Plans renew only when the
  client pays (a link 3 days before the end; 5 business days' grace, then paused).
  Refunds through the providers are not built yet.
- Private files use local disk in development and Cloudflare R2 in production
  (`PRIVATE_STORAGE_BACKEND=r2`); unknown backends are refused.
- In-app messaging and the website CMS are Phase 5+ and out of scope here.
- SQLite cannot exercise real row-lock races; PostgreSQL is required in
  production and concurrency tests should run against it.
- Notification outbox handling exists, but in-app notification delivery is not
  implemented yet.

This project does not claim compliance certification or that any deployment is
"100% secure".
