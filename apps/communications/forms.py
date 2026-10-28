from __future__ import annotations

from django import forms

from apps.accounts.forms import StyledFormMixin

from .models import SupportPriority


class SupportTicketForm(StyledFormMixin, forms.Form):
    subject = forms.CharField(max_length=160, label="Subject")
    priority = forms.ChoiceField(
        choices=SupportPriority.choices,
        initial=SupportPriority.MEDIUM,
        label="Priority",
    )
    body = forms.CharField(
        label="How can we help?",
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Do not include passwords, card numbers or other secrets.",
    )

    def clean_subject(self):
        subject = (self.cleaned_data.get("subject") or "").strip()
        if len(subject) < 5:
            raise forms.ValidationError("Give the ticket a clearer subject.")
        return subject

    def clean_body(self):
        body = (self.cleaned_data.get("body") or "").strip()
        if len(body) < 10:
            raise forms.ValidationError("Add a little more detail (10 characters or more).")
        if len(body) > 4000:
            raise forms.ValidationError("That message is too long.")
        return body
