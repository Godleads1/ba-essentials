from django.urls import path

from . import views

urlpatterns = [
    path("catalogue/", views.catalogue_home, name="staff-catalogue"),
    path(
        "catalogue/versions/<uuid:version_id>/state/",
        views.set_state,
        name="staff-catalogue-state",
    ),
]
