from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core import ratelimit
from apps.core.exceptions import RateLimited, ValidationFailure
from apps.organizations.access import memberships_for

from . import services
from .forms import (
    ChangePasswordForm,
    LoginForm,
    MFAConfirmForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    SignupForm,
    StepUpForm,
    VerifyForm,
)
from .models import ChallengePurpose, User, UserSession


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("client-overview")
    form = SignupForm(request.POST or None)
    if request.method == "POST":
        if not ratelimit.hit(
            "signup", _client_ip(request), limit=5, window_seconds=600
        ):
            messages.error(request, "Too many sign-up attempts. Please try again later.")
            return render(request, "auth/signup.html", {"form": form}, status=429)
        if form.is_valid():
            try:
                user = services.register_user(
                    email=form.cleaned_data["email"],
                    full_name=form.cleaned_data["full_name"],
                    password=form.cleaned_data["password1"],
                )
            except ValidationFailure as exc:
                form.add_error(None, exc.message)
            except RateLimited:
                pass
            else:
                request.session["pending_verify_email"] = form.cleaned_data["email"]
                if user is not None:
                    messages.success(request, "Check your email for a 6-digit code.")
                else:
                    messages.info(
                        request,
                        "If this address can be used, we have sent a verification code.",
                    )
                return redirect("verify-email")
    return render(request, "auth/signup.html", {"form": form})


@require_http_methods(["GET", "POST"])
def verify_email(request):
    email = request.session.get("pending_verify_email")
    if not email:
        return redirect("signup")
    form = VerifyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(email=email).first()
        if user is None:
            messages.error(request, "That code is invalid or has expired.")
        else:
            try:
                user = services.verify_challenge(
                    user, ChallengePurpose.EMAIL_VERIFICATION, form.cleaned_data["code"]
                )
            except (ValidationFailure, RateLimited) as exc:
                form.add_error("code", exc.message)
            else:
                request.session.pop("pending_verify_email", None)
                services.establish_session(request, user)
                messages.success(request, "Your email is verified. Welcome aboard.")
                if memberships_for(user).exists():
                    return redirect("client-overview")
                return redirect("organization-onboarding")
    return render(request, "auth/verify.html", {"form": form, "email": email})


@require_http_methods(["GET", "POST"])
def resend_verification(request):
    email = request.session.get("pending_verify_email")
    if email and request.method == "POST":
        user = User.objects.filter(email=email).first()
        if user and user.email_verified_at is None:
            try:
                services.send_challenge(user, ChallengePurpose.EMAIL_VERIFICATION)
                messages.info(request, "A new code is on its way.")
            except (RateLimited, ValidationFailure) as exc:
                messages.error(request, exc.message)
        else:
            messages.info(request, "A new code is on its way.")
    return redirect("verify-email")


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("staff-dashboard" if request.user.staff_role else "client-overview")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        outcome = services.login_with_password(
            email=form.cleaned_data["email"],
            password=form.cleaned_data["password"],
            client_ip=_client_ip(request),
        )
        if outcome.status == services.LoginStatus.OK:
            services.establish_session(request, outcome.user)
            next_url = request.GET.get("next")
            if next_url:
                from apps.core.middleware import _safe_next

                return redirect(_safe_next(request))
            return redirect(
                "staff-dashboard" if outcome.user.staff_role else "client-overview"
            )
        if outcome.status == services.LoginStatus.MFA_REQUIRED:
            request.session["pending_mfa_user"] = str(outcome.user.pk)
            request.session["pending_mfa_cv"] = outcome.user.credential_version
            return redirect("login-mfa")
        if outcome.status == services.LoginStatus.UNVERIFIED:
            request.session["pending_verify_email"] = outcome.user.email
            messages.info(request, "Verify your email address to continue.")
            return redirect("verify-email")
        form.add_error(None, "Email or password is incorrect.")
    return render(request, "auth/login.html", {"form": form})


@require_http_methods(["GET", "POST"])
def login_mfa(request):
    user_id = request.session.get("pending_mfa_user")
    if not user_id:
        return redirect("login")
    user = User.objects.filter(pk=user_id).first()
    if user is None or request.session.get("pending_mfa_cv") != user.credential_version:
        request.session.pop("pending_mfa_user", None)
        return redirect("login")
    form = VerifyForm(request.POST or None)
    if request.method == "POST":
        if not ratelimit.hit(
            "login-mfa", f"{user_id}:{_client_ip(request)}", limit=6, window_seconds=300
        ):
            form.add_error(None, "Too many attempts. Please sign in again.")
        elif form.is_valid():
            if services.verify_mfa(user, form.cleaned_data["code"]):
                request.session.pop("pending_mfa_user", None)
                request.session.pop("pending_mfa_cv", None)
                services.establish_session(request, user)
                services.mark_step_up(request, user)
                return redirect("staff-dashboard" if user.staff_role else "client-overview")
            form.add_error("code", "That authenticator code is not valid.")
    return render(request, "auth/mfa.html", {"form": form})


@require_POST
def logout_view(request):
    services.end_session(request)
    messages.info(request, "You have signed out.")
    return redirect("login")


@require_http_methods(["GET", "POST"])
def password_reset_request(request):
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        if not ratelimit.hit("pwreset", f"{email}:{_client_ip(request)}", limit=5, window_seconds=900):
            messages.info(request, "If that account exists, a reset code is on its way.")
            return redirect("login")
        user = User.objects.filter(email=email).first()
        if user is not None:
            try:
                services.send_challenge(user, ChallengePurpose.PASSWORD_RESET)
            except (RateLimited, ValidationFailure):
                pass
        request.session["pending_reset_email"] = email
        messages.info(request, "If that account exists, a reset code is on its way.")
        return redirect("password-reset-confirm")
    return render(request, "auth/password_reset.html", {"form": form})


@require_http_methods(["GET", "POST"])
def password_reset_confirm(request):
    email = request.session.get("pending_reset_email")
    if not email:
        return redirect("password-reset")
    form = PasswordResetConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(email=email).first()
        if user is None:
            form.add_error(None, "That code is invalid or has expired.")
        else:
            try:
                user = services.verify_challenge(
                    user, ChallengePurpose.PASSWORD_RESET, form.cleaned_data["code"]
                )
                services.reset_password(user, form.cleaned_data["new_password1"])
            except (ValidationFailure, RateLimited) as exc:
                form.add_error(None, exc.message)
            else:
                request.session.pop("pending_reset_email", None)
                messages.success(
                    request, "Your password has been reset. Sign in with the new one."
                )
                return redirect("login")
    return render(request, "auth/password_reset_confirm.html", {"form": form, "email": email})


@require_http_methods(["GET", "POST"])
def step_up(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={reverse('step-up')}")
    user = request.user
    next_url = request.GET.get("next") or request.POST.get("next") or "/app/"
    form = StepUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        password_ok = services.verify_step_up_password(
            user, form.cleaned_data["password"]
        )
        code_ok = True
        if user.mfa_enabled:
            code_ok = services.verify_mfa(user, form.cleaned_data["code"])
        if password_ok and code_ok:
            services.mark_step_up(request, user)
            from apps.core.middleware import _safe_next

            return redirect(_safe_next(request, next_url))
        form.add_error(None, "Those details are not correct.")
    return render(request, "auth/step_up.html", {"form": form, "next": next_url})


@login_required
@require_http_methods(["GET"])
def security_home(request):
    user = request.user
    sessions = UserSession.objects.filter(user=user, revoked_at__isnull=True)[:20]
    memberships = memberships_for(user).select_related("organization")
    return render(
        request,
        "app/security/home.html",
        {
            "sessions": sessions,
            "current_session_key": request.session.session_key,
            "memberships": memberships,
            "step_up_fresh": services.has_fresh_step_up(request, user),
            "password_form": ChangePasswordForm(),
        },
    )


@login_required
@require_http_methods(["POST"])
def change_password(request):
    form = ChangePasswordForm(request.POST)
    if form.is_valid():
        try:
            services.change_password(
                request.user,
                current_password=form.cleaned_data["current_password"],
                new_password=form.cleaned_data["new_password1"],
                request=request,
            )
        except ValidationFailure as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "Password changed. Other sessions were signed out.")
    else:
        messages.error(request, "Check the password fields and try again.")
    return redirect("security-home")


@login_required
@require_http_methods(["GET", "POST"])
def security_mfa(request):
    user = request.user
    if not services.has_fresh_step_up(request, user):
        return redirect(f"{reverse('step-up')}?next={reverse('security-mfa')}")

    secret = None
    form = MFAConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.confirm_mfa_enrollment(user, form.cleaned_data["code"])
        except ValidationFailure as exc:
            form.add_error("code", exc.message)
            secret = _enrollment_secret(user)
        else:
            messages.success(request, "Two-factor authentication is active.")
            return redirect("security-home")
    elif request.method == "GET" and not user.mfa_enabled:
        secret = services.begin_mfa_enrollment(user)

    uri = None
    if secret:
        from . import mfa as mfa_helpers

        uri = mfa_helpers.provisioning_uri(secret, email=user.email)
    return render(
        request,
        "app/security/mfa.html",
        {"form": form, "secret": secret, "uri": uri, "enabled": user.mfa_enabled},
    )


def _enrollment_secret(user: User) -> str | None:
    user.refresh_from_db(fields=["mfa_secret_encrypted"])
    if not user.mfa_secret_encrypted:
        return None
    from . import mfa as mfa_helpers

    return mfa_helpers.decrypt_secret(user.mfa_secret_encrypted)


@login_required
@require_POST
def revoke_session(request, session_key: str):
    UserSession.objects.filter(user=request.user, session_key=session_key).update(
        revoked_at=timezone.now()
    )
    services.revoke_session(request.user, session_key)
    if session_key == request.session.session_key:
        services.end_session(request)
        return redirect("login")
    messages.success(request, "That session was signed out.")
    return redirect("security-home")
