from django.urls import path

from . import views

urlpatterns = [
    path("billing/", views.billing_home, name="billing-home"),
    path("plan/", views.plan_home, name="plan-home"),
    path("plan/checkout/return/", views.plan_checkout_return, name="plan-checkout-return"),
    path("plan/checkout/<slug:plan_slug>/", views.plan_checkout, name="plan-checkout"),
    path("plan/upgrade/<slug:plan_slug>/", views.plan_upgrade, name="plan-upgrade"),
    path("plan/cancel/", views.plan_cancel, name="plan-cancel"),
    path("plan/resume/", views.plan_resume, name="plan-resume"),
    path("plan/downgrade/", views.plan_downgrade, name="plan-downgrade"),
    path("plan/downgrade/undo/", views.plan_downgrade_undo, name="plan-downgrade-undo"),
    path("plan/card/remove/", views.card_remove, name="card-remove"),
    path("billing/invoices/<uuid:invoice_id>/pay/", views.invoice_checkout, name="invoice-checkout"),
]
