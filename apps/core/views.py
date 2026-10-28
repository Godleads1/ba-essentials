from __future__ import annotations

import json

from django.contrib import messages
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from .exceptions import DomainError
from .middleware import _safe_next


@require_GET
def landing(request):
    if request.user.is_authenticated:
        return redirect("staff-dashboard" if _is_staff(request.user) else "client-overview")
    from apps.catalogue.models import ServiceCategory
    from apps.website.services import published_pages

    categories = ServiceCategory.objects.filter(is_active=True).order_by("sort_order", "name")
    return render(
        request,
        "public/landing.html",
        {"categories": categories, "site_pages": published_pages()},
    )


def _is_staff(user) -> bool:
    return bool(getattr(user, "staff_role", ""))


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # pragma: no cover - depends on infrastructure
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def csrf_failure(request, reason=""):
    return render(
        request,
        "errors/403.html",
        {"reason": "Your form session expired or the request could not be verified."},
        status=403,
    )


def permission_denied_view(request, exception=None):
    return render(request, "errors/403.html", {"reason": ""}, status=403)


def not_found_view(request, exception=None):
    return render(request, "errors/404.html", {}, status=404)


def server_error_view(request):
    return render(request, "errors/500.html", {}, status=500)


def handle_domain_error(request, error: DomainError, *, redirect_to: str):
    """Translate a domain error into a safe user-facing response.

    HTMX callers receive the message in-band so a partial can render it;
    ordinary form posts get a redirect with a flash message.
    """
    if request.headers.get("HX-Request"):
        return HttpResponse(
            json.dumps({"ok": False, "code": error.code, "message": error.message}),
            status=error.http_status,
            content_type="application/json",
        )
    messages.error(request, error.message)
    return redirect(redirect_to)
