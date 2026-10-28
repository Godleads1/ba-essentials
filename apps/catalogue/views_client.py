from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.organizations.access import client_required

from . import services


@client_required
@require_GET
def catalogue(request):
    versions = services.submittable_versions()
    return render(request, "app/catalogue.html", {"versions": versions})
