from django.urls import path

from . import views_staff as views

urlpatterns = [
    path("finance/", views.finance_home, name="staff-finance"),
    path(
        "finance/invoices/<uuid:invoice_id>/verify/",
        views.verify_payment,
        name="staff-finance-verify",
    ),
    path("tasks/<uuid:task_id>/fees/lines/", views.fee_add_line, name="staff-fee-add-line"),
    path("fees/lines/<uuid:line_id>/remove/", views.fee_remove_line, name="staff-fee-remove-line"),
    path("fees/lines/<uuid:line_id>/paid-out/", views.fee_paid_out, name="staff-fee-paid-out"),
    path("fees/<uuid:fee_id>/submit/", views.fee_submit, name="staff-fee-submit"),
    path("fees/<uuid:fee_id>/approve/", views.fee_approve, name="staff-fee-approve"),
    path("fees/<uuid:fee_id>/return/", views.fee_return, name="staff-fee-return"),
    path("fees/<uuid:fee_id>/withdraw/", views.fee_withdraw, name="staff-fee-withdraw"),
]
