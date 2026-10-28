from django.urls import path

from . import views

urlpatterns = [
    path("<uuid:file_id>/download/", views.download, name="file-download"),
]
