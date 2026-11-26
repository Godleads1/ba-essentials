# Putting the site live on Hostinger

This takes about two hours the first time. You type commands into **PowerShell** on your
Windows PC, and, once connected, into the server. Lines in grey boxes are commands: copy
them exactly. Anything in `CAPITALS_LIKE_THIS` is something you replace.

**Never paste a password, key or code from these steps into a chat, an email or GitHub.**
Keep them in a password manager (Bitwarden is free).

## How it fits together

```
Visitor → Cloudflare (blocks attacks) → your Hostinger server
            Caddy (HTTPS) → the app (Django) → PostgreSQL database
                                             → Redis (rate limits)
                                             → ClamAV (virus scanner)
          Files and backups → Cloudflare R2 (two private buckets)
```

Everything on the server runs in Docker containers, described in `deploy/docker-compose.yml`.
Only Caddy can be reached from the internet, and only by Cloudflare. The database, Redis and
the virus scanner have no open ports at all.

## What you need first

- A **Hostinger KVM 2** VPS (2 vCPU, 8 GB, 100 GB).
- Your **domain added to Cloudflare** (free plan), with Cloudflare's name servers set at your
  domain registrar.
- Accounts and keys for: **Cloudflare R2** (two buckets), **Resend** (email), **Termii**
  (texts; optional at first), **Paystack** and **Flutterwave** (test keys first).

## 1. Make an SSH key on your PC

An SSH key lets you sign in to the server without a password. In PowerShell:

```powershell
ssh-keygen -t ed25519 -C "banda-server"
```

Press Enter to accept the place it's saved, then choose a passphrase (and keep it in your
password manager). Copy the **public** half to your clipboard:

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | Set-Clipboard
```

The file ending in `.pub` is safe to share. The other one (`id_ed25519`) never leaves your PC.

## 2. Set up the VPS in Hostinger

In hPanel, open the VPS and choose:

- **Operating system:** plain **Ubuntu 24.04** (not one with a control panel).
- **Location:** a data centre in **Europe**. The Privacy Policy needs to say where data is
  kept, so tell the lawyer which country you chose.
- **SSH key:** paste the key from step 1.
- **Root password:** make a long one and keep it in your password manager. After step 3 it
  only works from Hostinger's own browser terminal (useful if you're ever locked out).

Write down the server's **IP address**.

**Hostinger's own backups:** if your plan makes automatic VPS backups or snapshots, they hold
copies of the database for longer than the **two days** the Privacy Policy promises. Either
turn them off in hPanel or ask the lawyer to mention them. The app makes its own encrypted
backups (step 9).

## 3. Lock the server down

Connect (type `yes` the first time):

```powershell
ssh root@SERVER_IP
```

On the server, get the code and run the hardening script:

```bash
apt-get update && apt-get install -y git
git clone https://github.com/Godleads1/ba-essentials.git /opt/banda
bash /opt/banda/deploy/harden.sh
```

It asks you to choose a password for the `deploy` user. That password is only for `sudo`
(admin commands), never for signing in. It then:

- installs updates and turns on automatic security updates;
- creates the `deploy` user with your SSH key, and installs Docker;
- opens SSH, and opens the web ports **to Cloudflare only**;
- blocks addresses that keep failing to sign in;
- switches off root sign-in and passwords over SSH.

**Before closing that window**, open a **new** PowerShell window and check you can get in:

```powershell
ssh deploy@SERVER_IP
```

Only when that works, close the old window. From now on you always sign in as `deploy`.
Give the code to the deploy user:

```bash
sudo chown -R deploy:deploy /opt/banda
```

## 4. Point the domain at the server (Cloudflare)

In the Cloudflare dashboard for your domain:

1. **DNS:** add an **A** record for `@` and another for `www`, both pointing to `SERVER_IP`,
   with the cloud **orange** (Proxied).
2. **SSL/TLS → Overview:** set the mode to **Full (strict)**.
3. **SSL/TLS → Edge Certificates:** turn on **Always Use HTTPS**, and set the
   **Minimum TLS Version** to **1.2**.
4. **Security → WAF:** turn on the free **Cloudflare Managed Ruleset**, if your plan offers it.
5. Leave **Bot Fight Mode off**. It can block Paystack's and Flutterwave's payment
   notifications.

## 5. The certificate between Cloudflare and the server

In Cloudflare: **SSL/TLS → Origin Server → Create Certificate**. Keep the defaults (RSA,
your domain and `*.yourdomain`, 15 years) and click **Create**. Leave that page open.

On the server:

```bash
mkdir -p /opt/banda/deploy/certs
nano /opt/banda/deploy/certs/origin.pem
```

Paste the **Origin Certificate**, then press Ctrl+O, Enter, Ctrl+X to save. Then:

```bash
nano /opt/banda/deploy/certs/origin.key
```

Paste the **Private Key** and save the same way. Lock both files:

```bash
sudo chown root:root /opt/banda/deploy/certs/*
sudo chmod 600 /opt/banda/deploy/certs/*
```

Cloudflare shows the private key only once. If you lose it, make a new certificate.

## 6. Settings and passwords

```bash
cd /opt/banda/deploy
cp env.example .env
chmod 600 .env
```

Make the passwords and keys. Run each line and copy what it prints into the matching line of
`.env`:

```bash
openssl rand -hex 32    # run four times: POSTGRES_PASSWORD, BANDA_OWNER_PASSWORD, BANDA_APP_PASSWORD, REDIS_PASSWORD
python3 -c "import secrets; print(secrets.token_urlsafe(64))"                         # DJANGO_SECRET_KEY
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" # MFA_ENCRYPTION_KEY
```

Then fill in the rest:

```bash
nano .env
```

| Setting | What to put |
| --- | --- |
| `DOMAIN`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `APP_BASE_URL` | Your domain, as in the examples |
| `ADMIN_URL` | A word only you know, for example `ops-desk` (Django admin moves there) |
| `SECURITY_ALERT_EMAILS` | Your email: security alerts go there |
| `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Your Resend API key, and an address on your domain that Resend has verified |
| `R2_*`, `BACKUP_R2_*` | See [DEPLOYMENT.md](DEPLOYMENT.md#private-files-on-cloudflare-r2). Use two buckets: `banda-private` and `banda-backups` |
| `TERMII_*` | From your Termii dashboard (leave empty to send codes by email only) |
| `PAYMENTS_ENABLED` | Keep `false` until test payments work (step 11) |

Leave `BACKUP_ENCRYPTION_KEY` empty for now; step 9 makes it.

## 7. Start it

```bash
cd /opt/banda/deploy
docker compose build
docker compose up -d db redis
docker compose run --rm migrate
docker compose up -d
docker compose ps
```

All five services should say `Up`, and `web` should say `(healthy)` after about a minute.
ClamAV takes a few minutes the first time while it downloads virus signatures; uploads wait
until it's ready.

Load the plans and services:

```bash
docker compose exec web python manage.py seed_plans
docker compose exec web python manage.py bootstrap_catalogue catalogue/service_catalogue_v2.1.json
```

Create the superusers, one command each. Each command asks for that person's first
password. Give it to them in person, never by message, and they change it after their first
sign-in in Profile & security. Each of them sets up their own authenticator app.

```bash
docker compose exec web python manage.py create_staff EMAIL --superuser --name "FULL NAME" --phone PHONE
```

The superusers who must **never be removed** go in `.env`, as their emails separated by
commas:

```bash
nano .env      # PROTECTED_SUPERUSER_EMAILS=first@example.com,second@example.com
docker compose up -d web
```

Those accounts can't be deleted, demoted or switched off from the app (Django admin
included). Only someone with server access can change that list. Everyone else is added from
the **Team** screen in the staff app.

## 8. Scheduled jobs

```bash
sudo cp /opt/banda/deploy/banda.cron /etc/cron.d/banda
sudo chmod 644 /etc/cron.d/banda
```

| Job | When |
| --- | --- |
| Emails waiting to go out (`process_outbox`) | Every minute |
| Re-scan files the virus scanner missed (`scan_files`) | Every 15 minutes |
| Renewal invoices and reminders (`run_renewals`) | Hourly |
| Encrypted backup (`run_backups`) | 02:00 Lagos time |
| Privacy Policy deletions (`run_retention`) | 02:41 Lagos time |
| Restore test of the newest backup (`restore_check`) | The 3rd of each month |

Their logs are in `/var/log/banda/`.

## 9. Backups

Make the backup encryption key:

```bash
docker compose exec web python manage.py run_backups --new-key
```

Put the key it prints into `BACKUP_ENCRYPTION_KEY` in `.env`, **and** into your password
manager. Without it, no backup can ever be read. Then restart and test a backup and a restore:

```bash
docker compose up -d web
docker compose exec web python manage.py run_backups
docker compose exec web python manage.py restore_check
```

The last line should say `Restore check passed`.

In Cloudflare R2, add an **Object Lock** rule to the `banda-backups` bucket with the
**prefix `db/`** and a retention of **1 day**. Then nobody, not even someone with the server's
keys, can delete a database backup in its first day. Keep it below 2 days, so the app can
still delete backups on the Privacy Policy schedule. More: [BACKUPS.md](BACKUPS.md).

Don't lock the whole bucket. The file mirror under `files/` keeps a small list,
`files/_missing.json`, that is rewritten on every run, and a lock on it makes every backup
after the first fail with `ObjectLockedByBucketPolicy`. Mirrored copies of deleted files
are also removed after 23 hours, which a 1-day lock would block.

## 10. Check it's safe

On the server:

```bash
docker compose exec web python manage.py check --deploy
```

The only warning should be `security.W021` (HSTS preload, deliberately off).

From your PC:

- Open `https://yourdomain`: the site loads, with a padlock.
- Open `https://yourdomain/health/`: it shows `{"status": "ok"}`.
- In PowerShell, check that nobody can get round Cloudflare. This should **time out**:

  ```powershell
  curl.exe -k --max-time 10 https://SERVER_IP
  ```
- Open `https://yourdomain/admin/`: you should get "not found". Your `ADMIN_URL` address
  should open the sign-in page.

Then sign in at `/login/staff/` with your superuser, and set up two-factor with Google
Authenticator or Microsoft Authenticator.

## 11. Payments

1. Put the Paystack and Flutterwave **test** keys in `.env`. Paystack signs its webhooks with
   the secret key, so `PAYSTACK_WEBHOOK_SECRET` is the same value as `PAYSTACK_SECRET_KEY`.
   For Flutterwave, make a random `FLUTTERWAVE_WEBHOOK_HASH` on the server
   (`openssl rand -hex 32`) and paste the same value into Flutterwave's **Secret hash**.
2. In both dashboards, in **test mode**, set the webhook address to
   `https://yourdomain/webhooks/paystack/` and `https://yourdomain/webhooks/flutterwave/`.
   Leave Paystack's callback URL empty (the app sends its own).
3. In Flutterwave, under **Settings → Webhooks**, tick **Enable v3 webhooks**, **Receive
   webhook response in JSON format**, **Enable webhook retries** and **Enable resend webhook
   from the dashboard**. Without v3, Flutterwave sends an older format the app refuses
   ("Webhook is missing an event identifier").
4. While testing on the live server, set `PAYMENTS_ENABLED=true` **and** `SIGNUPS_PAUSED=true`
   (so strangers can't sign up and pay with test cards), then `docker compose up -d web`.
   The app needs both providers' keys before it will start with payments on.
5. Make test payments in **Chrome, Safari and Firefox**: a Nigerian business pays in naira
   through Paystack (test card `4084 0840 8408 4081`, CVV `408`), and a business in another
   country pays in dollars through Flutterwave (test card `5531 8866 5214 2950`, `09/32`, CVV
   `564`, PIN `3310`, OTP `12345`). "Payment confirmed" should show within seconds: the app
   asks the provider directly when the client comes back, and the webhook confirms it again.
   `docker compose logs web | grep "POST /webhooks"` should show `200` for each provider.
6. Set `PAYMENTS_ENABLED=false` and `SIGNUPS_PAUSED=false` again until you go live.
7. Only then swap in the **live** keys (and, in Flutterwave and Paystack, the live webhook
   settings), restart, and make one small real payment yourself.

## Putting changes live

When a pull request has been merged on GitHub:

```bash
ssh deploy@SERVER_IP
cd /opt/banda
bash deploy/update.sh
```

It takes a backup, builds, applies database changes and restarts. The site is down only for
the few seconds the app restarts.

**To undo an update**, the script prints the version you were on. Go back to it with
`git checkout THAT_VERSION`, then run `bash deploy/update.sh` again. If the update changed
the database, ask your developer before undoing it: restoring from a backup may be needed
([BACKUPS.md](BACKUPS.md)).

## When something goes wrong

| Problem | Try |
| --- | --- |
| The site is down | `cd /opt/banda/deploy && docker compose ps`, then `docker compose logs --tail 100 web` |
| Restart everything | `docker compose restart` |
| The server is full | `df -h`, then `docker system prune` (removes old images, never your data) |
| An attack or leak | [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md). The emergency switches (`TEXT_MESSAGES_PAUSED`, `SIGNUPS_PAUSED`, `PAYMENTS_ENABLED`) go in `.env`, then run `docker compose up -d web` |
| Locked out of SSH | Use the browser terminal in hPanel, sign in as root with the root password |

## What this setup does and doesn't protect

Done:

- Only Cloudflare can reach the web ports, and the database, Redis and ClamAV have no ports
  open at all.
- The app's database account can't create, change or delete tables, or switch off the audit
  protection. Only migrations use the owner account.
- The app container runs as a normal user, with a read-only file system and no extra powers.
- SSH: keys only, no root sign-in, and repeat offenders are blocked. Security updates install
  themselves.

Known limits:

- **The disk isn't encrypted** (security checklist 28). Hostinger VPS disks aren't, and
  encrypting the whole disk would mean typing a passphrase at every reboot. Mitigations:
  - files are in R2, which Cloudflare encrypts;
  - backups are encrypted before they leave the server;
  - authenticator secrets and sign-in codes are encrypted or hashed in the database.
  
  The remaining risk is someone with access to Hostinger's hardware reading the database.
- **No point-in-time recovery** (checklist 29). You can restore to the last nightly backup
  (at most a day's changes lost), not to any minute. That's normal at this size. Adding WAL
  archiving later is possible, but it keeps data for longer, so the Privacy Policy's two days
  would need changing.
- **One server.** If it fails, the site is down until it's rebuilt from these steps and the
  newest backup (about an hour).
