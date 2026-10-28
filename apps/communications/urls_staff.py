from django.urls import path

from . import views_staff as views

urlpatterns = [
    path("support/", views.support_queue, name="staff-support"),
    path("support/<uuid:ticket_id>/", views.support_detail, name="staff-support-detail"),
    path(
        "tasks/<uuid:task_id>/messages/",
        views.post_task_message,
        name="staff-task-message-post",
    ),
]
