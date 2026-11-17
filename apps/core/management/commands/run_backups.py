from django.core.management.base import BaseCommand, CommandError

from apps.audit import alerts
from apps.core import backup_crypto, backups


class Command(BaseCommand):
    help = (
        "Back up the database (encrypted) and mirror private files to the backup store, then "
        "delete backups older than BACKUP_KEEP_HOURS (48). Schedule it daily. "
        "Use --new-key once to make a BACKUP_ENCRYPTION_KEY."
    )

    def add_arguments(self, parser):
        parser.add_argument("--new-key", action="store_true", help="Print a new encryption key and stop.")
        parser.add_argument("--database-only", action="store_true")
        parser.add_argument("--files-only", action="store_true")

    def handle(self, *args, **options):
        if options["new_key"]:
            self.stdout.write(backup_crypto.generate_key())
            self.stdout.write("Store this as BACKUP_ENCRYPTION_KEY on the server AND in your password manager.")
            return
        try:
            if not options["files_only"]:
                self.stdout.write(f"Database: {backups.backup_database()}")
            if not options["database_only"]:
                self.stdout.write(f"Files: {backups.backup_files()}")
        except Exception as exc:
            alerts.count("ops.backup_failed", "backup", {"error": type(exc).__name__, "detail": str(exc)[:200]})
            raise CommandError(f"Backup failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Backups done."))
