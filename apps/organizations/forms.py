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
    confirm_authority = forms.BooleanField(
        label="I confirm I'm allowed to act for this business.",
        error_messages={"required": "Tick the box to confirm you can act for this business."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.core.countries import choices

        self.fields["country"].choices = [("", "Select a country")] + choices()


class InviteMemberForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label="Email address")
