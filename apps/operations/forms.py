from __future__ import annotations

from django import forms

from apps.accounts.forms import StyledFormMixin
from apps.accounts.models import StaffRole, User


class AssignForm(StyledFormMixin, forms.Form):
    specialist = forms.ModelChoiceField(
        queryset=User.objects.filter(
            staff_role=StaffRole.SPECIALIST, is_active=True, email_verified_at__isnull=False
        ).order_by("full_name"),
        label="Specialist",
        empty_label="Choose a specialist",
    )
    note = forms.CharField(max_length=500, required=False, label="Assignment note")


class WorkLogForm(StyledFormMixin, forms.Form):
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Progress note")
    minutes = forms.IntegerField(
        required=False, min_value=1, max_value=1440, label="Minutes spent"
    )
