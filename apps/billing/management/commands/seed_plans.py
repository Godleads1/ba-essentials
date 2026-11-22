"""Create or update the three published plans from the pricing page and Terms 5.1.

Safe to run again: plans are matched by slug and only these fields are set.
Prices are list prices in kobo. Payments stay off unless PAYMENTS_ENABLED.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.billing.models import Plan

PLANS = (
    # slug, name, rank, price (kobo), price (US cents), tasks a month, legal reviews, legal window (months),
    # yearly price (kobo)
    ("starter", "Starter", 1, 6_500_000, 5_500, 7, 1, 3, 66_000_000),
    ("growth", "Growth", 2, 9_750_000, 8_900, 20, 1, 1, 105_300_000),
    ("dedicated", "Dedicated", 3, 26_000_000, 24_900, 30, 4, 1, 280_800_000),
)


class Command(BaseCommand):
    help = "Create or update the Starter, Growth and Dedicated plans."

    def handle(self, *args, **options):
        for slug, name, rank, price, price_usd, tasks, legal, legal_months, price_year in PLANS:
            _, created = Plan.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "rank": rank,
                    "price_minor": price,
                    "price_usd_minor": price_usd,
                    "price_year_minor": price_year,  # the yearly totals in the Terms (5.1)
                    "currency": "NGN",
                    "monthly_task_allowance": tasks,
                    "legal_review_allowance": legal,
                    "legal_review_period_months": legal_months,
                    "is_active": True,
                },
            )
            self.stdout.write(f"{'Created' if created else 'Updated'} {name}.")
