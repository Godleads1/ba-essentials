from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.billing import plan_rules
from apps.organizations.access import client_required

from . import services


@client_required
@require_GET
def catalogue(request):
    versions = list(services.submittable_versions())
    access = {}
    for version in versions:
        category = version.definition.category
        if category.pk not in access:
            access[category.pk] = plan_rules.category_access(request.organization, category)
        version.access = access[category.pk]
    return render(request, "app/catalogue.html", {"versions": versions})
