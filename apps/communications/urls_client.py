from django.urls import path

from . import views_client as views

urlpatterns = [
    path("messages/", views.messages_home, name="messages-home"),
    path(
        "messages/tasks/<uuid:task_id>/",
        views.post_task_message,
        name="task-message-post",
    ),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/read/", views.mark_all_read, name="notifications-read"),
    path("support/", views.support_home, name="support-home"),
    path("support/new/", views.support_new, name="support-new"),
    path("support/<uuid:ticket_id>/", views.support_detail, name="support-detail"),
    path("support/<uuid:ticket_id>/close/", views.close_ticket, name="support-close"),
]
