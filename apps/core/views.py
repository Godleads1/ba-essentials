from __future__ import annotations

import json

from django.contrib import messages
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from .exceptions import DomainError


@require_GET
def landing(request):
    if request.user.is_authenticated:
        return redirect("staff-dashboard" if _is_staff(request.user) else "client-overview")
    from apps.catalogue.models import ServiceCategory
    from apps.website.services import published_pages

    categories = ServiceCategory.objects.filter(is_active=True).order_by("sort_order", "name")
    pricing_data, prices = _landing_prices()
    service_counts, service_total = _service_counts()
    from apps.catalogue import guides

    return render(
        request,
        "banda/public/landing.html",
        {"categories": categories, "site_pages": published_pages(), "pricing_data": pricing_data, "prices": prices,
         "service_counts": service_counts, "service_total": service_total, "guides": guides.industries()},
    )


def _service_counts():
    """How many tasks each category on the landing page links to (by slug, with - as _), and the total."""
    from django.db.models import Count

    from apps.catalogue.services import submittable_versions

    every = submittable_versions()
    counts = {row["definition__category__slug"].replace("-", "_"): row["n"]
              for row in every.order_by().values("definition__category__slug").annotate(n=Count("pk"))}
    counts["creator"] = every.filter(definition__creator=True).count()
    return counts, every.count()


def _landing_prices():
    """Plan prices for the landing page, from the plans and billing settings in the app.

    Returns the data the price switch uses, and the Nigeria monthly prices for the page as served.
    """
    from apps.billing import pricing
    from apps.billing.models import BillingSettings, Plan
    from apps.core.countries import REGION_INTERNATIONAL, REGION_NIGERIA
    from apps.core.exceptions import ValidationFailure

    def naira(minor: int) -> str:
        return f"₦{minor // 100:,}"

    data = {"plans": {}, "discount": BillingSettings.current().yearly_discount_pct, "legal": {}}
    shown: dict = {}
    for plan in Plan.objects.filter(is_active=True, price_minor__gt=0):
        entry = {}
        for region in (REGION_NIGERIA, REGION_INTERNATIONAL):
            try:
                month = pricing.plan_price(plan, region, "month")
                year = pricing.plan_price(plan, region, "year")
            except ValidationFailure:
                continue
            entry[region] = {"month": month.amount_minor, "year": year.amount_minor, "currency": month.currency}
        data["plans"][plan.slug] = entry
        if REGION_NIGERIA in entry:
            shown[plan.slug] = {"ng_month": naira(entry[REGION_NIGERIA]["month"])}
    for region in (REGION_NIGERIA, REGION_INTERNATIONAL):
        minor, currency = pricing.extra_legal_fee(region)
        if minor:
            data["legal"][region] = {"minor": minor, "currency": currency}
    if REGION_NIGERIA in data["legal"]:
        shown["legal_ng"] = naira(data["legal"][REGION_NIGERIA]["minor"])
    return data, shown


def _is_staff(user) -> bool:
    return bool(getattr(user, "staff_role", ""))


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001  # pragma: no cover - any database failure means "unavailable"
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
