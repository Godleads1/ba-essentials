from django.urls import path

from . import views_public as views

urlpatterns = [
    path("services/", views.services_index, name="services"),
    path("services/<slug:slug>/", views.service_detail, name="service-detail"),
]
