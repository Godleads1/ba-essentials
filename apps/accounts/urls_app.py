from django.urls import path

from . import views

urlpatterns = [
    path("security/", views.security_home, name="security-home"),
    path("security/password/", views.change_password, name="security-password"),
    path("security/mfa/", views.security_mfa, name="security-mfa"),
    path(
        "security/sessions/<str:session_key>/revoke/",
        views.revoke_session,
        name="security-session-revoke",
    ),
]
