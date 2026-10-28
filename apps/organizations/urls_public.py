from django.urls import path

from . import views

urlpatterns = [
    path("invite/<str:token>/", views.accept_invitation, name="invitation-accept"),
]
