from django.core.management.base import BaseCommand

from apps.billing import renewals


class Command(BaseCommand):
    help = (
        "Issue renewal invoices with a reminder 3 days before a monthly plan ends, 14 before a yearly one (nothing is charged "
        "automatically), and handle the 5-business-day grace period, pauses and plan endings. Safe to run repeatedly; schedule it at least daily (hourly is fine)."
    )

    def handle(self, *args, **options):
        counts = renewals.run()
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k} {v}" for k, v in counts.items())))
