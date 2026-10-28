from __future__ import annotations

from django import forms

INPUT_CLASS = (
    "w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm "
    "text-slate-900 shadow-sm outline-none transition focus:border-emerald-600 "
    "focus:ring-2 focus:ring-emerald-100"
)
CHECKBOX_CLASS = (
    "h-4 w-4 rounded border-slate-300 text-emerald-700 focus:ring-emerald-500"
)


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", CHECKBOX_CLASS)
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", INPUT_CLASS)
            else:
                field.widget.attrs.setdefault("class", INPUT_CLASS)
                field.widget.attrs.setdefault("autocomplete", "off")


class SignupForm(StyledFormMixin, forms.Form):
    full_name = forms.CharField(max_length=150, label="Full name")
    email = forms.EmailField(label="Work email")
    password1 = forms.CharField(
        label="Password", widget=forms.PasswordInput, help_text="At least 12 characters."
    )
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)
    accept_terms = forms.BooleanField(
        label="I agree to the terms of service and privacy notice"
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password1") != cleaned.get("password2"):
            self.add_error("password2", "Both passwords must match.")
        return cleaned


class LoginForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label="Email")
    password = forms.CharField(label="Password", widget=forms.PasswordInput)


class VerifyForm(StyledFormMixin, forms.Form):
    code = forms.CharField(
        label="Verification code",
        max_length=6,
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}
        ),
    )


class PasswordResetRequestForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label="Email")


class PasswordResetConfirmForm(StyledFormMixin, forms.Form):
    code = forms.CharField(
        label="Reset code",
        max_length=6,
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}
        ),
    )
    new_password1 = forms.CharField(label="New password", widget=forms.PasswordInput)
    new_password2 = forms.CharField(label="Confirm new password", widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_password1") != cleaned.get("new_password2"):
            self.add_error("new_password2", "Both passwords must match.")
        return cleaned


class ChangePasswordForm(StyledFormMixin, forms.Form):
    current_password = forms.CharField(
        label="Current password", widget=forms.PasswordInput
    )
    new_password1 = forms.CharField(label="New password", widget=forms.PasswordInput)
    new_password2 = forms.CharField(label="Confirm new password", widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_password1") != cleaned.get("new_password2"):
            self.add_error("new_password2", "Both passwords must match.")
        return cleaned


class MFAConfirmForm(StyledFormMixin, forms.Form):
    code = forms.CharField(
        label="Authenticator code",
        max_length=6,
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}
        ),
    )


class StepUpForm(StyledFormMixin, forms.Form):
    password = forms.CharField(label="Password", widget=forms.PasswordInput)
    code = forms.CharField(
        label="Authenticator code (if enabled)",
        max_length=6,
        required=False,
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}
        ),
    )
