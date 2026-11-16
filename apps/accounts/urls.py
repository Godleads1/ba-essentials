from django.urls import path

from . import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("verify/", views.verify_email, name="verify-email"),
    path("verify/resend/", views.resend_verification, name="verify-resend"),
    path("login/", views.login_view, name="login"),
    path("login/code/", views.login_code, name="login-code"),
    path("login/code/resend/", views.login_code_resend, name="login-code-resend"),
    path("login/staff/", views.staff_login, name="staff-login"),
    path("verify/phone/", views.verify_phone, name="verify-phone"),
    path("login/mfa/", views.login_mfa, name="login-mfa"),
    path("logout/", views.logout_view, name="logout"),
    path("password-reset/", views.password_reset_request, name="password-reset"),
    path(
        "password-reset/confirm/",
        views.password_reset_confirm,
        name="password-reset-confirm",
    ),
    path("step-up/", views.step_up, name="step-up"),
]
