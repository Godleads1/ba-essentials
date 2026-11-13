from django.urls import path

from . import views

urlpatterns = [
    path("billing/", views.billing_home, name="billing-home"),
    path("plan/", views.plan_home, name="plan-home"),
    path("plan/checkout/return/", views.plan_checkout_return, name="plan-checkout-return"),
    path("plan/checkout/<slug:plan_slug>/", views.plan_checkout, name="plan-checkout"),
]
