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
    """Service Catalogue: what can be requested, grouped by category, with search."""
    every = services.submittable_versions()
    categories = []
    seen = set()
    for version in every:
        category = version.definition.category
        if category.pk not in seen:
            seen.add(category.pk)
            categories.append(category)
    query = (request.GET.get("q") or "").strip()[:80]
    chosen = request.GET.get("category", "")
    versions = every
    if chosen:
        versions = versions.filter(definition__category__slug=chosen)
    if query:
        versions = versions.filter(Q(definition__name__icontains=query) | Q(scope_summary__icontains=query)
                                   | Q(deliverables_summary__icontains=query)
                                   | Q(definition__category__name__icontains=query))
    versions = list(versions)
    access = {}
    for version in versions:
        category = version.definition.category
        if category.pk not in access:
            access[category.pk] = plan_rules.category_access(request.organization, category)
        version.access = access[category.pk]
    return render(request, "banda/app/catalogue.html", {
        "versions": versions, "categories": categories, "chosen": chosen, "query": query,
        "filtered": bool(chosen or query), "total": every.count(),
    })
