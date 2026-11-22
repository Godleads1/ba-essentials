from __future__ import annotations

from .models import MenuPlacement
from .services import menu_pages


def site_menu(request):
    """Published pages for the public header (the query only runs if a template uses it), and the
    addresses search engines see: built from APP_BASE_URL, not from the request's Host header."""
    from django.conf import settings

    from .seo import site_url

    return {
        "header_pages": menu_pages(MenuPlacement.HEADER),
        "site_url": site_url(),
        "canonical_url": site_url() + request.path,
        "google_site_verification": settings.GOOGLE_SITE_VERIFICATION,
        "bing_site_verification": settings.BING_SITE_VERIFICATION,
    }
