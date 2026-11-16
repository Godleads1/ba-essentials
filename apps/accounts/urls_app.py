from django.urls import path

from . import views

urlpatterns = [
    path("security/", views.security_home, name="security-home"),
    path("security/password/", views.change_password, name="security-password"),
    path("security/mfa/", views.security_mfa, name="security-mfa"),
    path("security/details/", views.security_details, name="security-details"),
    path("security/preferences/", views.security_preferences, name="security-preferences"),
    path("activity/", views.business_activity_view, name="business-activity"),
    path("security/codes/", views.security_channel, name="security-channel"),
    path("security/email/", views.security_email, name="security-email"),
    path("security/phone/", views.security_phone, name="security-phone"),
    path("security/phone/remove/", views.security_phone_remove, name="security-phone-remove"),
    path("security/mfa/off/", views.security_mfa_off, name="security-mfa-off"),
    path("security/sessions/others/revoke/", views.security_sessions_others, name="security-sessions-others"),
    path(
        "security/sessions/<str:session_key>/revoke/",
        views.revoke_session,
        name="security-session-revoke",
    ),
]
