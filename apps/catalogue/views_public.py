from __future__ import annotations

from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.website.services import published_pages

from . import services
from .models import ServiceCategory

SORT_OPTIONS = (
    ("category", "By category"),
    ("name", "Name A to Z"),
    ("turnaround", "Fastest turnaround"),
)
_SORT_ORDERING = {
    "category": ("definition__category__sort_order", "definition__category__name", "definition__name"),
    "name": ("definition__name",),
    "turnaround": ("turnaround_days", "definition__name"),
}
MAX_QUERY_LENGTH = 80


@require_GET
def services_index(request):
    """Public service catalogue: only services a client could actually request."""
    versions = services.submittable_versions()
    categories = list(
        ServiceCategory.objects.filter(
            is_active=True, pk__in=versions.values("definition__category_id")
        ).order_by("sort_order", "name")
    )

    query = request.GET.get("q", "").strip()[:MAX_QUERY_LENGTH]
    sort = request.GET.get("sort", "category")
    if sort not in _SORT_ORDERING:
        sort = "category"
    selected = next(
        (c for c in categories if c.slug == request.GET.get("category", "")), None
    )

    has_catalogue = bool(categories)
    if selected is not None:
        versions = versions.filter(definition__category=selected)
    if query:
        versions = versions.filter(
            Q(definition__name__icontains=query)
            | Q(scope_summary__icontains=query)
            | Q(definition__category__name__icontains=query)
        )
    versions = versions.order_by(*_SORT_ORDERING[sort])

    return render(
        request,
        "public/services.html",
        {
            "versions": versions,
            "categories": categories,
            "selected_category": selected,
            "query": query,
            "sort": sort,
            "sort_options": SORT_OPTIONS,
            "has_catalogue": has_catalogue,
            "is_filtered": bool(query or selected),
            "site_pages": published_pages(),
        },
    )


@require_GET
def service_detail(request, slug):
    version = services.submittable_versions().filter(definition__slug=slug).first()
    if version is None:
        raise Http404("Service not found")
    related = (
        services.submittable_versions()
        .filter(definition__category=version.definition.category)
        .exclude(pk=version.pk)[:3]
    )
    return render(
        request,
        "public/service_detail.html",
        {"version": version, "related": related, "site_pages": published_pages()},
    )
