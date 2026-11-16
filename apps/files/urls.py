from django.urls import path

from . import views

urlpatterns = [
    path("", views.files_home, name="files-home"),
    path("<uuid:file_id>/download/", views.download, name="file-download"),
]
