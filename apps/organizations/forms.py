from __future__ import annotations

from django import forms

from apps.accounts.forms import StyledFormMixin


class OrganizationForm(StyledFormMixin, forms.Form):
    name = forms.CharField(
        max_length=150, label="Business or team name", widget=forms.TextInput
    )
    country = forms.ChoiceField(
        label="Where is your business based?",
        help_text="This sets your plan's currency. Contact us if it ever needs to change.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.core.countries import choices

        self.fields["country"].choices = [("", "Select a country")] + choices()


class InviteMemberForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label="Email address")
