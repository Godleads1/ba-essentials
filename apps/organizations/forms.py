from __future__ import annotations

from django import forms

from apps.accounts.forms import StyledFormMixin


class OrganizationForm(StyledFormMixin, forms.Form):
    name = forms.CharField(
        max_length=150, label="Business or team name", widget=forms.TextInput
    )


class InviteMemberForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label="Email address")
