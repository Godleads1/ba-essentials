from django.urls import path

from . import views

urlpatterns = [
    path("billing/", views.billing_home, name="billing-home"),
]
