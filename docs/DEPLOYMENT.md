# Deployment

The site runs on one **Hostinger KVM 2 VPS** (Ubuntu 24.04) with Docker, behind Cloudflare.
The step-by-step guide is **[HOSTINGER.md](HOSTINGER.md)**. The files it uses are in `deploy/`:

| File | What it is |
| --- | --- |
| `deploy/docker-compose.yml` | Caddy, the app (Gunicorn + Django), PostgreSQL 17, Redis and ClamAV |
| `deploy/Caddyfile` | HTTPS with a Cloudflare Origin Certificate; `www` redirects to the main domain |
| `deploy/postgres-init/01-banda-roles.sh` | The database accounts: `banda_owner` for migrations, `banda_app` (rows only) for the site |
| `deploy/env.example` | Every setting the server needs; copied to `deploy/.env`, which is never committed |
| `deploy/harden.sh` | One-time server set-up: updates, the `deploy` user, Docker, firewall, fail2ban, SSH keys only |
| `deploy/cloudflare-firewall.sh` | Opens ports 80 and 443 to Cloudflare's address ranges only (weekly refresh) |
| `deploy/banda.cron` | The scheduled jobs |
| `deploy/update.sh` | Puts the latest `main` live: backup, build, migrate, restart, health check |

The image is built from the repository's `Dockerfile` (Python 3.13, `--require-hashes`, a
non-root user). The stylesheets are committed prebuilt (`static/banda/`), so the build needs no
Node step. After editing a staff template, rebuild `static/banda/staff.css` with
`npm ci && npm run build` in `tools/banda-css`; CI checks it's current.

## Visitor address behind a proxy

Rate limits need each visitor's real address (`apps/core/client_ip.py`). On the Hostinger
server, `deploy/docker-compose.yml` sets `CLIENT_IP_HEADER=HTTP_CF_CONNECTING_IP`. That is safe
only because the firewall lets Cloudflare alone reach the server, and Cloudflare overwrites
that header. Behind a different setup, use `TRUSTED_PROXY_COUNT=1` for one proxy that appends
to `X-Forwarded-For`. With neither, `check --deploy` warns (`banda.W001`).

## Private files on Cloudflare R2

1. In Cloudflare, create an R2 bucket (for example `banda-private`). Leave public
   access **off** and add no custom domain: files are only ever served through the
   app's authorised download page.
2. Create an R2 API token with **Object Read & Write** on that bucket only.
3. Set in `deploy/.env`:
   `PRIVATE_STORAGE_BACKEND=r2`, `R2_ENDPOINT_URL=https://<account id>.r2.cloudflarestorage.com`,
   `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
4. Production refuses to start with R2 chosen and any of these missing, and
   `check --deploy` warns (`banda.W003`) while files are still on local disk.

The malware scanner gets a temporary copy of each upload, deleted straight after.

## Backups

See [BACKUPS.md](BACKUPS.md). In short: an encrypted database backup and a copy of the
private files go to a separate R2 bucket every night, are kept for 48 hours, and a restore is
tested every month.

## Scheduled jobs

All in `deploy/banda.cron`, run inside the web container as the `deploy` user. Each is safe to
run again.

- `process_outbox`: sends waiting emails. Every minute.
- `scan_files`: re-scans files the virus scanner couldn't check at upload. Every 15 minutes.
- `run_renewals`: sends renewal invoices with reminders (3 days before a monthly plan ends,
  14 before a yearly one; nothing is charged automatically), then handles the 5 business days'
  grace, the pause and the end of a plan. Hourly.
- `run_backups` and `restore_check`: daily and monthly; see [BACKUPS.md](BACKUPS.md).
- `run_retention`: closes accounts whose paid period has ended, and deletes data on the
  Privacy Policy schedule. That covers the account and business profile 90 days after
  closing, task documents and support threads 6 years after they close, one-time codes after
  a day, and audit IP addresses after 90 days. A business under **legal hold** (ticked in
  admin) is skipped. Daily.

## Checks before going live

```bash
docker compose exec web python manage.py check --deploy
docker compose exec web python manage.py restore_check
```

Production refuses to start with:
- an unsafe secret key or wildcard hosts;
- SQLite, or a local cache;
- missing HTTPS or HSTS;
- console email, or no real virus scanner;
- payments switched on without their keys.

The only expected warning is `security.W021`: HSTS preload is deliberately off, because it's a
long-lived commitment.
