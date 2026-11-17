from django.core.management.base import BaseCommand, CommandError

from apps.audit import alerts
from apps.core import backups


class Command(BaseCommand):
    help = (
        "Prove the newest database backup can be restored: decrypt it, read it with pg_restore, "
        "and, with RESTORE_CHECK_DATABASE_URL (a scratch database, never the live one), restore it "
        "and compare row counts. Run before launch and monthly. After any real restore, run "
        "`run_retention` straight away so deletions are reapplied (Privacy Policy section 5)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--restore-url", default=None, help="Scratch database URL (overrides the setting).")

    def handle(self, *args, **options):
        try:
            result = backups.restore_check(restore_url=options["restore_url"])
        except Exception as exc:
            alerts.count("ops.restore_check_failed", "restore", {"error": type(exc).__name__, "detail": str(exc)[:200]})
            raise CommandError(f"Restore check failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Restore check passed: {result}"))
