from __future__ import annotations

from django import forms

from apps.accounts.forms import StyledFormMixin
from apps.catalogue.services import submittable_versions

from .models import Priority

MAX_ATTACHMENTS = 5


class SubmitTaskForm(StyledFormMixin, forms.Form):
    service_version = forms.ModelChoiceField(
        queryset=submittable_versions(),
        label="Service",
        empty_label="Choose a service",
    )
    title = forms.CharField(max_length=160, label="Short title")
    brief = forms.CharField(
        label="What do you need?",
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Describe the work in at least 20 characters.",
    )
    desired_outcome = forms.CharField(
        label="Desired outcome",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    priority = forms.ChoiceField(
        choices=Priority.choices, initial=Priority.MEDIUM, label="Priority"
    )
    due_date = forms.DateField(
        required=False,
        label="Preferred due date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean_title(self):
        title = (self.cleaned_data.get("title") or "").strip()
        if len(title) < 4:
            raise forms.ValidationError("Give the request a clearer title.")
        return title

    def clean_brief(self):
        brief = (self.cleaned_data.get("brief") or "").strip()
        if len(brief) < 20:
            raise forms.ValidationError("Add a little more detail (20 characters or more).")
        return brief
