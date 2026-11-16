from django.urls import path

from . import views_webhook as views

urlpatterns = [
    path("webhooks/paystack/", views.paystack_webhook, name="paystack-webhook"),
    path("webhooks/flutterwave/", views.flutterwave_webhook, name="flutterwave-webhook"),
]
