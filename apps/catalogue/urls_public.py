from django.urls import path

from . import views_public

urlpatterns = [
    path("services/", views_public.services_page, name="public-services"),
]
