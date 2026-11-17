# Backups and restoring

The Privacy Policy (section 5) promises encrypted backups that are **rotated
within two days**, with **deletions reapplied after a restore**. This is how the
platform keeps that promise. Code: `apps/core/backups.py`.

## What is backed up

| What | How | Kept |
| --- | --- | --- |
| Database | `pg_dump`, encrypted on the server with `BACKUP_ENCRYPTION_KEY` (AES-256-GCM), uploaded as `db/<time>.dump.enc` | 48 hours (`BACKUP_KEEP_HOURS`) |
| Private files | Copied to `files/<key>` in the backup bucket | Until a day after the file is deleted, then removed (within two days) |
| Lightsail managed database | Lightsail's own automatic snapshots | Set the retention in Lightsail to **2 days** to match the policy |

Backups go to a **separate private R2 bucket** (`BACKUP_R2_BUCKET`), never the
files bucket. Ideally give it its own API token (`BACKUP_R2_ACCESS_KEY_ID`,
`BACKUP_R2_SECRET_ACCESS_KEY`), so the app's everyday token can't touch backups.

## One-time setup

1. Make the encryption key, and store it **in two places**: the server's
   environment and the owner's password manager. Without it no backup can be read.
   ```
   python manage.py run_backups --new-key
   ```
2. Create the R2 bucket `banda-backups` (public access off), and a token for it.
3. Set `BACKUP_BACKEND=r2`, `BACKUP_R2_BUCKET`, `BACKUP_ENCRYPTION_KEY` (and the
   optional backup token) on the server.
4. Schedule, as systemd timers or cron:
   - `python manage.py run_backups`: daily (for example 02:00 Lagos time);
   - `python manage.py restore_check`: monthly, with `RESTORE_CHECK_DATABASE_URL`
     set to an empty scratch database (never the live one; the command refuses it).
5. Before launch, run `restore_check` once by hand and keep its output.

A failed backup or restore check sends a security alert (`SECURITY_ALERT_EMAILS`)
and exits with an error, so the timer shows it as failed.

## Restoring for real

1. Put the site in maintenance (stop the web service and timers).
2. Download the newest `db/<time>.dump.enc` and decrypt it on the server:
   ```
   python manage.py shell -c "from apps.core import backup_crypto as b; b.decrypt_stream(open('db.dump.enc','rb'), open('db.dump','wb'))"
   ```
3. Restore into the (empty) database:
   `pg_restore --clean --if-exists --no-owner --no-privileges --dbname=<db> db.dump`
4. Copy any missing files back from `files/` in the backup bucket to the files bucket.
5. **Run `python manage.py run_retention` straight away**, so anything deleted
   since the backup is deleted again (Privacy Policy section 5).
6. Restart the services, check `/health/`, sign in as a test client and staff
   user, and record the restore in the incident log (docs/INCIDENT_RESPONSE.md).
