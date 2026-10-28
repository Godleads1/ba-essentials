from django.urls import path

from . import views

urlpatterns = [
    path(
        "tasks/<uuid:task_id>/deliverable/decision/",
        views.decide,
        name="deliverable-decide",
    ),
]
