# Staging deployment (Render, Redis, Resend, ClamAV)

This runbook describes the staging stack defined in `render.yaml`. It is a
staging environment, not a production launch: use synthetic data, keep
payments disabled, and do not treat it as evidence of compliance.

## What the Blueprint creates

All resources use the Frankfurt region so private networking works.

| Resource | Blueprint type | Purpose |
| --- | --- | --- |
| `ba-essentials-staging` | Docker web service | Gunicorn + WhiteNoise |
| `ba-essentials-staging-db` | PostgreSQL 17 | Application database |
| `ba-essentials-staging-cache` | Render Key Value | Shared Redis cache for rate limits |
| `ba-essentials-staging-clamav` | Private image service | clamd on TCP 3310 |
| `ba-essentials-staging-outbox` | Cron job | Delivers pending email events every 5 minutes |
| `ba-essentials-staging-files` | 1 GB disk | Private uploads at `/var/data/private` |
| `ba-essentials-staging-clamav-db` | 1 GB disk | Persists ClamAV signatures across restarts |

The web service, PostgreSQL instance, ClamAV service, and cron job are paid
resources. Only Key Value is on the free plan. Review the prices shown in the
Render dashboard before applying the Blueprint; a fully working staging stack
is not free.

The container builds the Tailwind CSS, installs production Python
dependencies, collects hashed static files, runs as an unprivileged `app`
user, and drops privileges after fixing the persistent-disk ownership.

## Prerequisites

1. A Render account with access to the private GitHub repository
   `Godleads1/ba-essentials`, and the Render GitHub app installed for it.
2. A Resend account and an API key with sending permission.
3. A verified sending domain in Resend, or, for a first test, Resend's
   `onboarding@resend.dev` sender (which can only deliver to the account
   owner's address).
4. The Render CLI (`winget install --id Render.CLI -e`) for validation and
   deploy inspection.

## First deployment

1. Push the repository to GitHub. The `render.yaml` Blueprint is read from the
   branch.
2. In the Render dashboard choose **New → Blueprint**, select the repository,
   and apply `render.yaml`.
3. Render prompts for the three values marked `sync: false`:
   - `DJANGO_SECRET_KEY`: at least 50 characters. Generate one in a terminal
     with `openssl rand -base64 48` (or use the dashboard's generate button)
     and paste it into Render. Do not put it in chat, a ticket, or git.
   - `EMAIL_HOST_PASSWORD`: the Resend API key.
   - `DEFAULT_FROM_EMAIL`: a sender on the verified Resend domain, for example
     `B & A Essentials <staging@your-domain.test>`.
4. Wait for the ClamAV service to download its signature database on first
   start. Uploads attempted before clamd is ready are marked failed, not clean.
5. Confirm the web deploy became live. `preDeployCommand` runs
   `python manage.py migrate --noinput` before the new instance starts.

`RENDER_EXTERNAL_HOSTNAME` is adopted automatically by
`config.settings.staging`, so a service-name suffix does not break host or CSRF
validation. `APP_BASE_URL` in the environment group is only the expected
hostname used by the production fail-closed checks at import time.

## Verify the deployment

Replace `<host>` with the service hostname printed by Render.

```bash
curl -fsS "https://<host>/health/"
# {"status": "ok"}

curl -sSI "https://<host>/" | grep -Ei 'strict-transport|x-content-type|x-frame|referrer-policy'
# HSTS and the application security headers are present.
```

Check the shared cache from a Render shell on the web service:

```bash
python manage.py shell -c "from django.core.cache import cache; cache.set('staging-check','ok',30); print(cache.get('staging-check'))"
```

Check the SMTP path with a synthetic message (use your own address):

```bash
python manage.py shell -c "from django.core.mail import send_mail; print(send_mail('Staging check','Synthetic message from staging.',None,['you@example.test']))"
```

Check the private ClamAV TCP path from a Render shell:

```bash
printf 'staging scan check\n' > /tmp/clam-check.txt
printf 'TCPSocket 3310\nTCPAddr %s\n' "$CLAMD_HOST" > /tmp/clamd-check.conf
clamdscan --stream --config-file=/tmp/clamd-check.conf /tmp/clam-check.txt
```

Then run one synthetic end-to-end flow: register a test client, read the OTP
from the delivered email, sign in, submit a request with a synthetic PDF, and
confirm the staff queue sees it. Do not upload real client documents.

The cron job's output is visible under **Logs** on
`ba-essentials-staging-outbox`. A successful run prints
`Delivered N; failed 0.`

## Updating

The web service and cron job deploy when checks pass on `main`. Schema changes
run through `preDeployCommand`; review each migration and keep a Render
PostgreSQL snapshot before a destructive change.

## Rollback

1. Roll back to the previous deploy in the Render dashboard (or redeploy the
   previous commit).
2. Restore the PostgreSQL instance from its latest snapshot if a migration
   changed data irreversibly.
3. Do not rotate `DJANGO_SECRET_KEY` as part of a rollback: existing MFA
   enrolments are encrypted with a key derived from it.

## Known staging limitations

- **One web instance.** Render persistent disks cannot be mounted by multiple
  instances or by cron jobs. `scan_files` is therefore not scheduled: a cron
  container would not see the web service's uploads. Re-scan on the web
  instance, or add an object-storage adapter before relying on scheduled
  re-scans.
- **Payments are disabled.** Paystack checkout initialization is not
  implemented; enabling payments requires the Paystack secrets and a finance
  review.
- **No in-app messaging or CMS.** Those surfaces are Phase 5+.
- **HSTS preload is off** (`security.W021`). It is a long-lived commitment and
  needs an explicit decision.
- **Synthetic data only.** Staging is not a production environment and its
  logs, database, and disk must not contain real client material.

## Local validation performed before deployment

```bash
python -m pytest
python manage.py check --deploy --settings=config.settings.staging  # with staging env
python manage.py collectstatic --noinput --ignore "src/*" --settings=config.settings.build
render blueprints validate ./render.yaml
```

`--ignore "src/*"` keeps the Tailwind source file out of the hashed static
build; the compiled `static/css/app.css` is still collected.
