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
            if isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect)):
                field.widget.attrs.setdefault("class", CHECKBOX_CLASS)
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", INPUT_CLASS)
            else:
                field.widget.attrs.setdefault("class", INPUT_CLASS)
                field.widget.attrs.setdefault("autocomplete", "off")


CHANNEL_LABELS = {"whatsapp": "WhatsApp", "sms": "Text message (SMS)", "email": "Email"}
# For sentences: "we've sent a code by WhatsApp / text message / email".
CHANNEL_IN_TEXT = {"whatsapp": "WhatsApp", "sms": "text message", "email": "email"}


class SignupForm(StyledFormMixin, forms.Form):
    """Clients have no password: they get a one-time code each time they sign in."""

    full_name = forms.CharField(max_length=150, label="Full name")
    email = forms.EmailField(label="Work email", help_text="We send your first code here.")
    phone = forms.CharField(
        max_length=32, required=False, label="Phone number",
        help_text="Optional. For codes by WhatsApp or text message, e.g. 0803 123 4567 or +44 7700 900123.",
        widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel"}),
    )
    channel = forms.ChoiceField(label="Send my sign-in codes by", widget=forms.RadioSelect)
    accept_terms = forms.BooleanField(
        label="I agree to the terms of service and privacy notice"
    )

    def __init__(self, *args, channels=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["channel"].choices = [(c, CHANNEL_LABELS[c]) for c in channels]
        self.fields["channel"].initial = channels[0] if channels else "email"
        if list(channels) == ["email"]:
            self.fields["channel"].widget = forms.HiddenInput()
            self.fields["phone"].widget = forms.HiddenInput()


class CodeLoginForm(StyledFormMixin, forms.Form):
    identifier = forms.CharField(
        max_length=254, label="Phone number or email",
        widget=forms.TextInput(attrs={"autocomplete": "username", "autocapitalize": "none", "spellcheck": "false"}),
    )
    channel = forms.ChoiceField(label="Send my code by", widget=forms.RadioSelect, required=False)
    remember = forms.BooleanField(required=False, label="Keep me signed in on this device")

    def __init__(self, *args, channels=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["channel"].choices = [(c, CHANNEL_LABELS[c]) for c in channels]
        self.fields["channel"].initial = channels[0] if channels else ""
        if not channels:
            self.fields["identifier"].label = "Email"
            self.fields["channel"].widget = forms.HiddenInput()


class ChannelForm(forms.Form):
    channel = forms.ChoiceField(widget=forms.RadioSelect)

    def __init__(self, *args, channels=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["channel"].choices = [(c, CHANNEL_LABELS[c]) for c in channels]


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
