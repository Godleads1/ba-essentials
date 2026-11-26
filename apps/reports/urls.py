from django.urls import path

from . import views

urlpatterns = [
    path("summary/", views.monthly_summary, name="monthly-summary"),
    path("reports/", views.reports_home, name="reports-home"),
    path("reports/export.csv", views.reports_csv, name="reports-csv"),
]
