from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.files import services
from apps.files.models import PrivateFile, ScanStatus


class Command(BaseCommand):
    help = "Re-run the malware scan for files that are pending or failed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true", help="Rescan every stored file"
        )
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        if settings.FILE_SCAN_BACKEND == "noop":
            raise CommandError(
                "FILE_SCAN_BACKEND=noop does not scan anything. Configure a real "
                "scanner before running this command."
            )

        queryset = PrivateFile.objects.all()
        if not options["all"]:
            queryset = queryset.filter(
                scan_status__in=[ScanStatus.PENDING, ScanStatus.FAILED]
            )
        files = list(queryset.order_by("created_at")[: max(1, options["limit"])])

        for private_file in files:
            services.rescan(private_file)
            self.stdout.write(
                f"{private_file.pk}: {private_file.scan_status}"
            )

        self.stdout.write(self.style.SUCCESS(f"Rescanned {len(files)} file(s)."))
