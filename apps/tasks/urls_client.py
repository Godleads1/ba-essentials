from django.urls import path

from . import views_client as views

urlpatterns = [
    path("", views.overview, name="client-overview"),
    path("tasks/", views.task_list, name="task-list"),
    path("tasks/new/", views.task_submit, name="task-submit"),
    path("tasks/<uuid:task_id>/", views.task_detail, name="task-detail"),
    path(
        "tasks/<uuid:task_id>/information/",
        views.provide_information,
        name="task-provide-information",
    ),
    path("tasks/<uuid:task_id>/cancel/", views.cancel_task, name="task-cancel"),
]
