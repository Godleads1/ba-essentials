from django.urls import path

from . import views

urlpatterns = [
    path("summary/", views.monthly_summary, name="monthly-summary"),
]
