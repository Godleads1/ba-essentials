from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import staff_permission_required
from apps.accounts.permissions import CATALOGUE_MANAGE
from apps.core.exceptions import DomainError

from . import services
from .models import ServiceDefinition, ServiceVersion


@staff_permission_required(CATALOGUE_MANAGE)
@require_http_methods(["GET"])
def catalogue_home(request):
    definitions = (
        ServiceDefinition.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related("versions")
        .order_by("category__sort_order", "name")
    )
    return render(
        request,
        "staff/catalogue.html",
        {"definitions": definitions, "states": ServiceVersion._meta.get_field("state").choices},
    )


@staff_permission_required(CATALOGUE_MANAGE)
@require_POST
def set_state(request, version_id):
    version = ServiceVersion.objects.filter(pk=version_id, is_current=True).first()
    if version is None:
        messages.error(request, "That service version was not found.")
        return redirect("staff-catalogue")
    try:
        services.set_state(version, request.POST.get("state", ""), actor=request.user)
    except DomainError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, f"{version.definition.name} is now {version.get_state_display()}.")
    return redirect("staff-catalogue")
