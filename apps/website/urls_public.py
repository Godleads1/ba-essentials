from django.urls import path

from . import seo, views_public

urlpatterns = [
    path("robots.txt", seo.robots_txt, name="robots-txt"),
    path("sitemap.xml", seo.sitemap_xml, name="sitemap-xml"),
    path("favicon.ico", seo.favicon_ico, name="favicon"),
    path("contact/", views_public.contact, name="contact"),
    path("site-images/<uuid:image_id>/", views_public.site_image, name="website-image"),
    path("terms/", views_public.legal, {"doc": "terms"}, name="terms"),
    path("privacy/", views_public.legal, {"doc": "privacy"}, name="privacy"),
    path("cookies/", views_public.legal, {"doc": "cookies"}, name="cookies"),
]
