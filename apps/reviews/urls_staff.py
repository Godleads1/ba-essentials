from django.urls import path

from . import views_staff as views

urlpatterns = [
    path("reviews/", views.review_queue, name="staff-reviews"),
    path(
        "tasks/<uuid:task_id>/review/",
        views.decide,
        name="staff-review-decide",
    ),
    path(
        "tasks/<uuid:task_id>/release/",
        views.release,
        name="staff-release",
    ),
]
