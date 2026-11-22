from django.urls import path

from . import views_staff as views

urlpatterns = [
    path("website/", views.page_list, name="staff-website-pages"),
    path("website/new/", views.page_create, name="staff-website-create"),
    path("website/seo/", views.seo_overview, name="staff-website-seo"),
    path("website/pricing/", views.pricing_overview, name="staff-website-pricing"),
    path("website/publish/", views.publish_queue, name="staff-website-publish-queue"),
    path("website/images/", views.image_library, name="staff-website-images"),
    path("website/home/", views.home_edit, name="staff-website-home"),
    path("website/home/versions/", views.home_history, name="staff-website-home-history"),
    path("website/home/use-original/", views.home_use_original, name="staff-website-home-original"),
    path("website/home/<uuid:revision_id>/preview/", views.home_preview, name="staff-website-home-preview"),
    path("website/home/<uuid:revision_id>/publish/", views.home_publish, name="staff-website-home-publish"),
    path("website/home/<uuid:revision_id>/restore/", views.home_restore, name="staff-website-home-restore"),
    path("website/images/<uuid:image_id>/delete/", views.image_delete, name="staff-website-image-delete"),
    path("website/<uuid:page_id>/", views.page_edit, name="staff-website-edit"),
    path(
        "website/<uuid:page_id>/preview/",
        views.page_preview,
        name="staff-website-preview",
    ),
    path(
        "website/<uuid:page_id>/history/",
        views.page_history,
        name="staff-website-history",
    ),
    path(
        "website/<uuid:page_id>/publish/",
        views.page_publish,
        name="staff-website-publish",
    ),
    path(
        "website/<uuid:page_id>/unpublish/",
        views.page_unpublish,
        name="staff-website-unpublish",
    ),
    path(
        "website/<uuid:page_id>/restore/",
        views.page_restore,
        name="staff-website-restore",
    ),
    path("website/<uuid:page_id>/menu/", views.page_menu, name="staff-website-menu"),
    path("leads/", views.leads, name="staff-leads"),
    path("leads/<uuid:contact_id>/", views.lead_update, name="staff-lead-update"),
]
