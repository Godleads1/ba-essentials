from django.urls import path

from . import views_public

urlpatterns = [
    path("contact/", views_public.contact, name="contact"),
    path("site-images/<uuid:image_id>/", views_public.site_image, name="website-image"),
    path("terms/", views_public.legal, {"doc": "terms"}, name="terms"),
    path("privacy/", views_public.legal, {"doc": "privacy"}, name="privacy"),
    path("cookies/", views_public.legal, {"doc": "cookies"}, name="cookies"),
]
