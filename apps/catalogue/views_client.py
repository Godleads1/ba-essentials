from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.organizations.access import client_required

from . import browse


@client_required
@require_GET
def catalogue(request):
    """Service Catalogue: categories first, then the tasks in one, with search and industry filters."""
    context = browse.build(request.GET, organization=request.organization)
    return render(request, "banda/app/catalogue.html", context)
