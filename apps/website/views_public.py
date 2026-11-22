from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from apps.core import ratelimit
from apps.core.client_ip import client_ip

from . import services
from .forms import ContactForm


def _client_ip(request) -> str:
    return client_ip(request)


@require_GET
def page(request, slug):
    page_obj = services.published_page(slug)
    if page_obj is None:
        raise Http404("Page not found")
    return render(
        request,
        "banda/public/page.html",
        {
            "page": page_obj,
            "revision": page_obj.published_revision,
            "site_pages": services.published_pages(),
        },
    )


@require_GET
def legal(request, doc):
    """Terms of Service, Privacy Policy and Cookie Notice, as written for the launch."""
    from apps.core.legal import TERMS_EFFECTIVE_DATE

    return render(request, f"banda/public/{doc}.html",
                  {"site_pages": services.published_pages(), "terms_effective": TERMS_EFFECTIVE_DATE})


@require_http_methods(["GET", "POST"])
def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST":
        ip = _client_ip(request)
        if not ratelimit.hit("public-contact", ip, limit=5, window_seconds=3600):
            messages.error(request, "Too many messages from this connection. Try again later.")
            return render(
                request,
                "banda/public/contact.html",
                {"form": form, "site_pages": services.published_pages(), "support_email": settings.SUPPORT_EMAIL},
                status=429,
            )
        if form.is_valid():
            if form.cleaned_data.get("website"):
                return redirect("contact")
            services.submit_contact_message(
                name=form.cleaned_data["name"],
                email=form.cleaned_data["email"],
                phone=form.cleaned_data.get("phone", ""),
                company=form.cleaned_data.get("company", ""),
                message=form.cleaned_data["message"],
                ip=ip,
            )
            return redirect(f"{reverse('contact')}?sent=1")
    return render(
        request,
        "banda/public/contact.html",
        {"form": form, "site_pages": services.published_pages(), "support_email": settings.SUPPORT_EMAIL},
    )


@require_GET
def site_image(request, image_id):
    """An uploaded page image. Public once a published page uses it; until then only website staff see it."""
    from django.http import FileResponse

    from apps.accounts.permissions import WEBSITE_EDIT, has_perm
    from apps.files.storage import get_storage

    from . import images
    from .models import PageImage

    image = PageImage.objects.filter(pk=image_id).first()
    if image is None:
        raise Http404("Image not found")
    live = images.is_live(image)
    if not live and not has_perm(request.user, WEBSITE_EDIT):
        raise Http404("Image not found")  # the same answer as a missing image: nothing to learn
    storage = get_storage()
    if not storage.exists(image.storage_key):
        raise Http404("Image not found")
    response = FileResponse(storage.open(image.storage_key), content_type=image.content_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    # Published images can be cached (by browsers and Cloudflare); drafts never are.
    response["Cache-Control"] = "public, max-age=3600" if live else "private, no-store"
    return response
