from django.urls import path

from . import views_public

urlpatterns = [
    path("contact/", views_public.contact, name="contact"),
]
