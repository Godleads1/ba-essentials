from django.core.management.base import BaseCommand

from apps.billing import renewals


class Command(BaseCommand):
    help = (
        "Send renewal reminders, charge saved cards, and handle grace periods, pauses and "
        "plan endings. Safe to run repeatedly; schedule it at least daily (hourly is fine)."
    )

    def handle(self, *args, **options):
        counts = renewals.run()
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k} {v}" for k, v in counts.items())))
