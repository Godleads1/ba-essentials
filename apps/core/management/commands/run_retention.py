from django.core.management.base import BaseCommand

from apps.core import retention


class Command(BaseCommand):
    help = (
        "Close accounts whose paid period has ended, and delete data on the Privacy Policy "
        "schedule (profiles 90 days after closing, task documents and support after 6 years). "
        "Safe to run repeatedly; schedule it daily."
    )

    def handle(self, *args, **options):
        counts = retention.run()
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k}: {v}" for k, v in counts.items())))
