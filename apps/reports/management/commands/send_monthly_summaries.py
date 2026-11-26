from django.core.management.base import BaseCommand, CommandError

from apps.reports import emails, summary


class Command(BaseCommand):
    help = "Queue last month's summary email for Growth and Dedicated owners (run on the 1st). Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument("--month", help="YYYY-MM to send instead of last month")

    def handle(self, *args, **options):
        from datetime import date

        if options.get("month"):
            try:
                year, month = (int(x) for x in options["month"].split("-"))
                target = date(year, month, 1)
            except ValueError as exc:
                raise CommandError("Use --month YYYY-MM.") from exc
            if target >= summary.this_month():
                raise CommandError("Only finished months can be sent.")
        else:
            target = summary.previous_month(summary.this_month())
        counts = emails.send_for_month(target)
        self.stdout.write(f"Monthly summaries for {target:%Y-%m}: {counts}")
