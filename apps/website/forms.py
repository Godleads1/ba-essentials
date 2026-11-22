from __future__ import annotations

from django import forms

from apps.accounts.forms import StyledFormMixin

from .images import MAX_ALT_TEXT
from .models import MenuPlacement, PageImage
from .services import MAX_BODY_LENGTH, MAX_META_DESCRIPTION


class ContactForm(StyledFormMixin, forms.Form):
    name = forms.CharField(max_length=120, label="Your name")
    email = forms.EmailField(label="Email")
    phone = forms.CharField(max_length=40, required=False, label="Phone (optional)")
    company = forms.CharField(max_length=150, required=False, label="Business name (optional)")
    message = forms.CharField(
        label="How can we help?",
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Do not include passwords, card numbers or other secrets.",
    )
    website = forms.CharField(
        required=False,
        label="Leave this field empty",
        widget=forms.HiddenInput,
    )

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if len(name) < 2:
            raise forms.ValidationError("Tell us your name.")
        return name

    def clean_message(self):
        message = (self.cleaned_data.get("message") or "").strip()
        if len(message) < 10:
            raise forms.ValidationError("Add a little more detail (10 characters or more).")
        if len(message) > 4000:
            raise forms.ValidationError("That message is too long.")
        return message


class PageDraftForm(StyledFormMixin, forms.Form):
    title = forms.CharField(max_length=160)
    summary = forms.CharField(
        max_length=255,
        required=False,
        help_text="One-line description shown under the title.",
    )
    body = forms.CharField(
        max_length=MAX_BODY_LENGTH,
        widget=forms.Textarea(attrs={"rows": 16}),
        help_text="Formatting guide on the right. HTML is shown as text, never run.",
    )
    meta_description = forms.CharField(
        max_length=MAX_META_DESCRIPTION,
        required=False,
        label="Search description",
        help_text="Shown by Google and in link previews. Up to 160 characters; leave empty to use the summary.",
    )
    hide_from_search = forms.BooleanField(
        required=False,
        label="Hide this page from search engines",
    )
    share_image = forms.ModelChoiceField(
        queryset=PageImage.objects.all(),
        required=False,
        label="Link-preview image",
        empty_label="None",
        help_text="Shown when the page is shared on WhatsApp, LinkedIn and similar. Upload it under Images first.",
    )


class PageCreateForm(PageDraftForm):
    slug = forms.SlugField(
        max_length=120,
        label="Page address",
        help_text="Used in the URL, for example /about-us/.",
    )
    field_order = ["slug", "title", "summary", "body", "meta_description", "hide_from_search", "share_image"]


class PageMenuForm(StyledFormMixin, forms.Form):
    menu = forms.ChoiceField(choices=MenuPlacement.choices, label="Show in the site menu")
    menu_order = forms.IntegerField(min_value=0, max_value=999, label="Position")


class ImageUploadForm(StyledFormMixin, forms.Form):
    image = forms.FileField(
        label="Image",
        help_text="JPEG, PNG or WebP, up to 5 MB. It is resized to at most 2000 pixels and its metadata (such as location) is removed.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )
    alt_text = forms.CharField(
        max_length=MAX_ALT_TEXT,
        label="Description (alt text)",
        help_text="What the image shows, for people using screen readers. For example: Our team at the Lekki office.",
    )


def home_copy_form_class():
    """A form with one field per editable home page text (apps/website/home.py)."""
    from . import home

    attrs = {}
    for key, field in home.FIELDS.items():
        widget = forms.Textarea(attrs={"rows": 3}) if field.kind in (home.TEXT, home.PLAIN) else None
        attrs[key] = forms.CharField(label=field.label, max_length=field.max_length, help_text=field.help,
                                     widget=widget)
    attrs["share_image"] = forms.ModelChoiceField(
        queryset=PageImage.objects.all(), required=False, label="Link-preview image", empty_label="None",
        help_text="Shown when the home page is shared on WhatsApp, LinkedIn and similar. Upload it under Images.",
    )
    return type("HomeCopyForm", (StyledFormMixin, forms.Form), attrs)


class FaqForm(StyledFormMixin, forms.Form):
    question = forms.CharField(max_length=160, required=False)
    answer = forms.CharField(max_length=1200, required=False, widget=forms.Textarea(attrs={"rows": 3}))


class _FaqFormSet(forms.BaseFormSet):
    def add_fields(self, form, index):
        # Django adds Position and Remove after the form's own styling ran, so style them here.
        from apps.accounts.forms import CHECKBOX_CLASS, INPUT_CLASS

        super().add_fields(form, index)
        if "ORDER" in form.fields:
            form.fields["ORDER"].label = "Position"
            form.fields["ORDER"].widget.attrs.update({"class": INPUT_CLASS, "min": "1"})
        if "DELETE" in form.fields:
            form.fields["DELETE"].widget.attrs.update({"class": CHECKBOX_CLASS})


def faq_formset_class():
    from .home import MAX_FAQS

    return forms.formset_factory(FaqForm, formset=_FaqFormSet, extra=2, can_delete=True, can_order=True,
                                 max_num=MAX_FAQS, validate_max=True)
