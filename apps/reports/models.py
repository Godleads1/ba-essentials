from __future__ import annotations

from datetime import date

from django.db import models


class MonthlySummarySent(models.Model):
    """One row per business and month once its summary email has been queued, so it's sent once."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE,
                                     related_name="monthly_summaries_sent")
    month = models.DateField()  # the first day of the month summarised
    recipients = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "month"], name="uniq_summary_per_month")]

    def __str__(self) -> str:
        return f"{self.organization} {self.month:%Y-%m}"

    @staticmethod
    def first_of(year: int, month: int) -> date:
        return date(year, month, 1)
