from django.urls import path

from . import views_staff as views

urlpatterns = [
    path(
        "tasks/<uuid:task_id>/quotes/",
        views.create_quote,
        name="staff-quote-create",
    ),
    path(
        "tasks/<uuid:task_id>/quotes/<uuid:version_id>/send/",
        views.send_quote,
        name="staff-quote-send",
    ),
]
