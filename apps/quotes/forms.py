from __future__ import annotations

from decimal import Decimal

from django import forms

from apps.accounts.forms import StyledFormMixin

MAX_AMOUNT_MINOR = 100_000_000_00


class QuoteForm(StyledFormMixin, forms.Form):
    scope_summary = forms.CharField(
        label="Scope",
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="What is included in this piece of work.",
    )
    exclusions = forms.CharField(
        label="Exclusions",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    amount = forms.DecimalField(
        label="Amount (NGN)",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
    )
    turnaround_days = forms.IntegerField(
        label="Turnaround (days)", min_value=1, max_value=365
    )
    revision_allowance = forms.IntegerField(
        label="Included revisions", min_value=0, max_value=10, initial=1
    )
    payment_terms = forms.CharField(
        label="Payment terms",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean_scope_summary(self):
        scope = (self.cleaned_data.get("scope_summary") or "").strip()
        if len(scope) < 10:
            raise forms.ValidationError("Describe the scope in a little more detail.")
        return scope

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        minor = int((amount * 100).to_integral_value())
        if minor > MAX_AMOUNT_MINOR:
            raise forms.ValidationError("That amount is too large for one request.")
        return amount

    @property
    def amount_minor(self) -> int:
        return int((self.cleaned_data["amount"] * 100).to_integral_value())
