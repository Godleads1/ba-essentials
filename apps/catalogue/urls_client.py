from django.urls import path

from . import views_client as views

urlpatterns = [
    path("catalogue/", views.catalogue, name="service-catalogue"),
]
