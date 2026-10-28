from django.urls import path

from . import views_staff as views

urlpatterns = [
    path("finance/", views.finance_home, name="staff-finance"),
    path(
        "finance/invoices/<uuid:invoice_id>/verify/",
        views.verify_payment,
        name="staff-finance-verify",
    ),
]
