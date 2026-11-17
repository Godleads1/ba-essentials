from __future__ import annotations

from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from . import browse


@require_GET
def services_page(request):
    """The Service Catalogue for visitors: what we do, by category and industry, before signing up.

    Signed-in clients get the same page inside the app, where they can request a task.
    """
    if request.user.is_authenticated and not getattr(request.user, "staff_role", ""):
        target = reverse("service-catalogue")
        return redirect(f"{target}?{request.GET.urlencode()}" if request.GET else target)
    return render(request, "banda/public/services.html", browse.build(request.GET, public=True))
