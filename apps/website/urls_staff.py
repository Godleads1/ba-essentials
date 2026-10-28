from django.urls import path

from . import views_staff as views

urlpatterns = [
    path("website/", views.page_list, name="staff-website-pages"),
    path("website/new/", views.page_create, name="staff-website-create"),
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
    path("leads/", views.leads, name="staff-leads"),
    path("leads/<uuid:contact_id>/", views.lead_update, name="staff-lead-update"),
]
