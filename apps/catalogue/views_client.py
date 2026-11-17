from __future__ import annotations

from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.billing import plan_rules
from apps.organizations.access import client_required

from . import services


@client_required
@require_GET
def catalogue(request):
    """Service Catalogue: categories first, then the tasks in one, with search and industry filters."""
    from .models import INDUSTRIES

    every = services.submittable_versions()
    counts: dict = {}
    categories = []
    creator_total = 0
    for version in every:
        category = version.definition.category
        if category.pk not in counts:
            categories.append(category)
            counts[category.pk] = 0
        counts[category.pk] += 1
        creator_total += version.definition.creator
    query = (request.GET.get("q") or "").strip()[:80]
    chosen = request.GET.get("category", "")
    industry = request.GET.get("industry", "").upper()
    industry = industry if industry in INDUSTRIES else ""
    creator = request.GET.get("view") == "creator"
    versions = every
    if creator:
        versions = versions.filter(definition__creator=True)
        chosen = ""
    elif chosen:
        versions = versions.filter(definition__category__slug=chosen)
    if industry:
        versions = versions.filter(definition__industries__contains=industry)
    if query:
        versions = versions.filter(Q(definition__name__icontains=query) | Q(definition__code__icontains=query)
                                   | Q(definition__subtype__icontains=query) | Q(scope_summary__icontains=query)
                                   | Q(deliverables_summary__icontains=query)
                                   | Q(definition__category__name__icontains=query))
    filtered = bool(chosen or query or industry or creator)
    access = {}
    for category in categories:
        access[category.pk] = plan_rules.category_access(request.organization, category)
        category.access = access[category.pk]
        category.count = counts[category.pk]
    versions = list(versions.order_by("definition__category__sort_order", "definition__code", "definition__name")) \
        if filtered else []
    for version in versions:
        version.access = access[version.definition.category_id]
    keep = request.GET.copy()
    keep.pop("industry", None)
    return render(request, "banda/app/catalogue.html", {
        "versions": versions, "categories": categories, "chosen": chosen, "query": query,
        "filtered": filtered, "total": every.count(), "industry": industry, "industries": INDUSTRIES,
        "creator": creator, "creator_total": creator_total, "keep": keep.urlencode(),
        "industry_label": INDUSTRIES.get(industry, ""),
    })
