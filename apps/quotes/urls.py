from django.urls import path

from . import views

urlpatterns = [
    path(
        "quotes/<uuid:version_id>/accept/",
        views.accept_quote,
        name="quote-accept",
    ),
    path(
        "quotes/<uuid:version_id>/reject/",
        views.reject_quote,
        name="quote-reject",
    ),
]
