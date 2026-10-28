from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("", core_views.landing, name="landing"),
    path("health/", core_views.health, name="health"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.organizations.urls_public")),
    path("", include("apps.billing.urls_webhook")),
    path("app/", include("apps.tasks.urls_client")),
    path("app/", include("apps.quotes.urls")),
    path("app/", include("apps.reviews.urls")),
    path("app/", include("apps.billing.urls")),
    path("app/", include("apps.organizations.urls")),
    path("app/", include("apps.accounts.urls_app")),
    path("app/", include("apps.catalogue.urls_client")),
    path("app/", include("apps.communications.urls_client")),
    path("app/files/", include("apps.files.urls")),
    path("staff/", include("apps.operations.urls")),
    path("staff/", include("apps.communications.urls_staff")),
    path("staff/", include("apps.quotes.urls_staff")),
    path("staff/", include("apps.reviews.urls_staff")),
    path("staff/", include("apps.billing.urls_staff")),
    path("staff/", include("apps.catalogue.urls")),
    path("admin/", admin.site.urls),
]

handler403 = "apps.core.views.permission_denied_view"
handler404 = "apps.core.views.not_found_view"
handler500 = "apps.core.views.server_error_view"
