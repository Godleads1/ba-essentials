from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.audit.activity import own_activity
from apps.core.client_ip import client_ip
from apps.core import ratelimit
from apps.core.exceptions import PermissionFailure, RateLimited, ValidationFailure
from apps.organizations.access import current_membership, memberships_for

from . import codes, phones, privacy, profile, services
from .forms import (
    CHANNEL_IN_TEXT,
    CHANNEL_LABELS,
    ChangePasswordForm,
    ChannelForm,
    CodeLoginForm,
    ContactPreferencesForm,
    DetailsForm,
    LoginForm,
    MFAConfirmForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    SignupForm,
    StepUpForm,
    VerifyForm,
)
from .models import ChallengePurpose, DataRequest, DataRequestKind, User, UserSession


def _client_ip(request) -> str:
    return client_ip(request)


def _after_sign_in(request, user):
    next_url = request.GET.get("next") or request.session.pop("sign_in_next", "")
    if next_url:
        from apps.core.middleware import _safe_next

        return redirect(_safe_next(request, next_url))
    if user.staff_role:
        return redirect("staff-dashboard")
    if memberships_for(user).exists():
        return redirect("client-overview")
    return redirect("organization-onboarding")


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("client-overview")
    channels = codes.channels_offered_at_signup()
    form = SignupForm(request.POST or None, channels=channels)
    if request.method == "POST" and form.is_valid():
        try:
            pending = codes.register_client(
                full_name=form.cleaned_data["full_name"],
                email=form.cleaned_data["email"],
                phone=form.cleaned_data.get("phone", ""),
                channel=form.cleaned_data.get("channel") or "email",
                client_ip=_client_ip(request),
            )
        except ValidationFailure as exc:
            form.add_error(None, exc.message)
        except RateLimited as exc:
            form.add_error(None, exc.message)
            return render(request, "banda/auth/signup.html", {"form": form}, status=429)
        else:
            request.session["pending_sign_in"] = pending
            return redirect("login-code")
    return render(request, "banda/auth/signup.html", {"form": form})


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
    return render(request, "banda/auth/verify_email.html", {"form": form, "email": email})


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
def admin_login_redirect(request):
    """Django admin has no sign-in form of its own.

    Its built-in form checked passwords without the app's rate limits, lockout or
    second factor, and answered differently for a right and a wrong password.
    Every request, GET or POST, is sent to the app's sign-in instead, and a POST
    is never read. Only an admin path is kept as the destination.
    """
    next_url = request.GET.get("next", "")
    if not next_url.startswith("/admin/") or next_url.startswith("//"):
        next_url = "/admin/"
    return redirect(f"{reverse('staff-login')}?{urlencode({'next': next_url})}")


@require_http_methods(["GET", "POST"])
def login_view(request):
    """Clients sign in with a one-time code. Staff use the staff sign-in page."""
    if request.user.is_authenticated:
        return redirect("staff-dashboard" if request.user.staff_role else "client-overview")
    channels = codes.phone_channels()
    form = CodeLoginForm(request.POST or None, channels=channels)
    if request.method == "POST" and form.is_valid():
        try:
            pending = codes.request_sign_in(
                form.cleaned_data["identifier"],
                client_ip=_client_ip(request),
                channel=form.cleaned_data.get("channel") or "",
            )
        except (ValidationFailure, RateLimited) as exc:
            form.add_error(None, exc.message)
        else:
            pending["remember"] = bool(form.cleaned_data.get("remember"))
            request.session["pending_sign_in"] = pending
            if request.GET.get("next"):
                request.session["sign_in_next"] = request.GET["next"]
            return redirect("login-code")
    return render(request, "banda/auth/login.html", {"form": form, "phone_ok": bool(channels)})


@require_http_methods(["GET", "POST"])
def login_code(request):
    pending = request.session.get("pending_sign_in")
    if not pending:
        return redirect("login")
    form = VerifyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = codes.verify_sign_in(pending, form.cleaned_data["code"], client_ip=_client_ip(request))
        except (ValidationFailure, RateLimited) as exc:
            form.add_error("code", exc.message)
        else:
            request.session.pop("pending_sign_in", None)
            if pending.get("signup"):
                profile.record_agreement(user)
            if user.mfa_enabled:
                request.session["pending_mfa_user"] = str(user.pk)
                request.session["pending_mfa_cv"] = user.credential_version
                request.session["pending_remember"] = bool(pending.get("remember"))
                return redirect("login-mfa")
            services.establish_session(request, user, remember=bool(pending.get("remember")))
            if pending.get("confirm_phone"):
                return redirect("verify-phone")
            return _after_sign_in(request, user)
    other = [c for c in codes.phone_channels() if c != pending.get("ch")] if pending.get("kind") == "phone" else []
    return render(request, "banda/auth/code.html", {
        "form": form, "pending": pending, "channel_label": CHANNEL_IN_TEXT.get(pending.get("ch"), "email"),
        "resend_wait": settings.OTP_RESEND_COOLDOWN_SECONDS if request.method == "GET" else 0,
        "other_channels": [(c, CHANNEL_IN_TEXT[c]) for c in other],
    })


@require_POST
def login_code_resend(request):
    pending = request.session.get("pending_sign_in")
    if not pending:
        return redirect("login")
    try:
        if pending.get("signup"):
            fresh = dict(pending)
            user = User.objects.filter(pk=pending.get("uid") or None).first() if pending.get("uid") else None
            if user is not None:
                codes._limit_sends(pending["key"], _client_ip(request))
                try:
                    codes.send_code(user, ChallengePurpose.SIGN_IN, "email")
                except (RateLimited, ValidationFailure):
                    pass
        else:
            fresh = codes.request_sign_in(pending["key"], client_ip=_client_ip(request),
                                          channel=request.POST.get("channel") or pending.get("ch", ""))
            fresh.update({k: v for k, v in pending.items() if k in ("confirm_phone", "remember")})
    except (ValidationFailure, RateLimited) as exc:
        messages.error(request, exc.message)
        return redirect("login-code")
    request.session["pending_sign_in"] = fresh
    messages.info(request, f"If the details are right, a new code is on its way to {fresh['to']}.")
    return redirect("login-code")


@require_http_methods(["GET", "POST"])
def staff_login(request):
    """Staff and Django admins: password and authenticator code."""
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
            if request.GET.get("next"):
                request.session["sign_in_next"] = request.GET["next"]
            return redirect("login-mfa")
        if outcome.status == services.LoginStatus.UNVERIFIED:
            request.session["pending_verify_email"] = outcome.user.email
            messages.info(request, "Verify your email address to continue.")
            return redirect("verify-email")
        form.add_error(None, "Email or password is incorrect.")
    return render(request, "banda/auth/staff_login.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def verify_phone(request):
    """Confirm a phone number with a code sent to it, so it can be used to sign in."""
    user = request.user
    if not user.phone or user.phone_verified:
        return _after_sign_in(request, user)
    form = VerifyForm(request.POST or None)
    if request.method == "POST" and request.POST.get("send"):
        try:
            to = codes.send_phone_code(user, client_ip=_client_ip(request), channel=request.POST.get("channel", ""))
            messages.info(request, f"We've sent a code to {to}.")
        except (ValidationFailure, RateLimited) as exc:
            messages.error(request, exc.message)
        return redirect("verify-phone")
    if request.method == "POST" and form.is_valid():
        try:
            codes.confirm_phone(user, form.cleaned_data["code"])
        except (ValidationFailure, RateLimited) as exc:
            form.add_error("code", exc.message)
        else:
            messages.success(request, "Your phone number is confirmed. You can sign in with it now.")
            return _after_sign_in(request, user)
    return render(request, "banda/auth/verify_phone.html", {
        "form": form, "phone": phones.masked(user.phone),
        "channels": [(c, CHANNEL_LABELS[c]) for c in codes.phone_channels()],
    })


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
                services.establish_session(request, user, remember=bool(request.session.pop("pending_remember", False)))
                services.mark_step_up(request, user)
                return _after_sign_in(request, user)
            form.add_error("code", "That authenticator code is not valid.")
    return render(request, "banda/auth/mfa.html", {"form": form})


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
        # Only staff have passwords; clients sign in with codes and get the same answer.
        if user is not None and not user.is_client_account:
            try:
                services.send_challenge(user, ChallengePurpose.PASSWORD_RESET)
            except (RateLimited, ValidationFailure):
                pass
        request.session["pending_reset_email"] = email
        messages.info(request, "If that account exists, a reset code is on its way.")
        return redirect("password-reset-confirm")
    return render(request, "banda/auth/password_reset.html", {"form": form})


@require_http_methods(["GET", "POST"])
def password_reset_confirm(request):
    email = request.session.get("pending_reset_email")
    if not email:
        return redirect("password-reset")
    form = PasswordResetConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(email=email).first()
        if user is None or user.is_client_account:
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
                return redirect("staff-login")
    return render(request, "banda/auth/password_reset_confirm.html", {"form": form, "email": email})


@require_http_methods(["GET", "POST"])
def step_up(request):
    """Confirm it's you before something sensitive.

    Staff give their password (and authenticator code). Clients have no password,
    so they get a one-time code (and give their authenticator code if they set one up).
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={reverse('step-up')}")
    user = request.user
    next_url = request.GET.get("next") or request.POST.get("next") or "/app/"
    by_code = not user.has_usable_password()
    form = (VerifyForm if by_code else StepUpForm)(request.POST or None)
    if by_code and request.method == "POST" and request.POST.get("send"):
        try:
            to = codes.send_step_up_code(user, client_ip=_client_ip(request))
            messages.info(request, f"We've sent a code to {to}.")
        except (ValidationFailure, RateLimited) as exc:
            messages.error(request, exc.message)
        return redirect(f"{reverse('step-up')}?{urlencode({'next': next_url})}")
    if request.method == "POST" and form.is_valid():
        if by_code:
            first_ok = codes.verify_step_up_code(user, form.cleaned_data["code"])
            code_ok = True
            if user.mfa_enabled:
                code_ok = services.verify_mfa(user, request.POST.get("totp", ""))
        else:
            first_ok = services.verify_step_up_password(user, form.cleaned_data["password"])
            code_ok = True
            if user.mfa_enabled:
                code_ok = services.verify_mfa(user, form.cleaned_data["code"])
        if first_ok and code_ok:
            services.mark_step_up(request, user)
            from apps.core.middleware import _safe_next

            return redirect(_safe_next(request, next_url))
        form.add_error(None, "Those details are not correct.")
    return render(request, "banda/auth/step_up.html", {
        "form": form, "next": next_url, "by_code": by_code,
        "channel_label": CHANNEL_IN_TEXT.get(codes.preferred_channel(user), "email") if by_code else "",
    })


def _needs_step_up(request, back: str = ""):
    """Sensitive changes need a fresh "confirm it's you". Returns a redirect, or None.

    After confirming, a GET page reopens itself; a button (POST) returns to `back`.
    """
    if services.has_fresh_step_up(request, request.user):
        return None
    target = request.get_full_path() if request.method == "GET" else (back or reverse("security-home"))
    return redirect(f"{reverse('step-up')}?{urlencode({'next': target})}")


def _keep_this_session(request, user):
    """After a change that signs out other devices, keep this one signed in."""
    request.session["cv"] = user.credential_version
    services.mark_step_up(request, user)


@login_required
@require_http_methods(["GET"])
def security_home(request):
    user = request.user
    sessions = UserSession.objects.filter(user=user, revoked_at__isnull=True)[:20]
    memberships = memberships_for(user).select_related("organization")
    channels = codes.channels_for(user) if user.is_client_account else []
    return render(
        request,
        "banda/app/security.html",
        {
            "sessions": sessions,
            "current_session_key": request.session.session_key,
            "memberships": memberships,
            "password_form": ChangePasswordForm() if user.has_usable_password() else None,
            "channels": [(c, "SMS" if c == "sms" else CHANNEL_LABELS[c]) for c in channels],
            "preferred": codes.preferred_channel(user) if channels else "",
            "masked_phone": phones.masked(user.phone),
            "phone_ok": bool(codes.phone_channels()),
            "membership": current_membership(request) if user.is_client_account else None,
            "activity": own_activity(user),
            "data_requests": DataRequest.objects.filter(user=user)[:3] if user.is_client_account else [],
            "dpo_email": settings.DPO_EMAIL,
        },
    )


def _owner_membership(request):
    membership = current_membership(request) if request.user.is_client_account else None
    if membership is None or not membership.is_owner:
        raise Http404
    return membership


@login_required
@require_http_methods(["GET", "POST"])
def security_close(request):
    """Close the business account: confirm it's you, see what happens, then tick to confirm."""
    from apps.organizations import closure

    membership = _owner_membership(request)
    org = membership.organization
    if org.closure_requested_at is not None:
        return redirect(reverse("security-home") + "#close")
    if (stop := _needs_step_up(request, reverse("security-close"))) is not None:
        return stop
    error = ""
    if request.method == "POST":
        if request.POST.get("confirm") != "yes":
            error = "Tick the box to confirm."
        else:
            try:
                org = closure.request_closure(org, membership)
            except (ValidationFailure, PermissionFailure) as exc:
                error = exc.message
            else:
                if org.closed_at:
                    messages.success(request, "Your account is closed. You can still sign in to view and download your files and invoices.")
                else:
                    messages.success(request, f"Your account will close on {org.closes_at:%d %b %Y}. Nothing more will be charged.")
                return redirect(reverse("security-home") + "#close")
    return render(request, "banda/app/close_account.html", {
        "organization": org, "closes_at": closure.paid_until(org), "open_requests": closure.open_requests(org),
        "error": error,
    }, status=400 if error else 200)


@login_required
@require_POST
def security_close_cancel(request):
    from apps.organizations import closure

    membership = _owner_membership(request)
    try:
        closure.cancel_closure(membership.organization, membership)
    except (ValidationFailure, PermissionFailure) as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "Your account will stay open.")
    return redirect(reverse("security-home") + "#close")


@login_required
@require_http_methods(["GET"])
def security_data_download(request):
    """Download my data, as JSON. Confirm it's you first: it holds everything."""
    if not request.user.is_client_account:
        raise Http404
    if (stop := _needs_step_up(request)) is not None:
        return stop
    response = HttpResponse(privacy.export_json(request.user), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="banda-my-data-{timezone.now():%Y-%m-%d}.json"'
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_http_methods(["GET", "POST"])
def security_data_request(request):
    if not request.user.is_client_account:
        raise Http404
    kinds = DataRequestKind.choices
    chosen, details, error = request.POST.get("kind", ""), request.POST.get("details", ""), ""
    if request.method == "POST":
        if not ratelimit.hit("data-request", str(request.user.pk), limit=10, window_seconds=3600):
            error = "Too many requests. Try again later."
        else:
            membership = current_membership(request)
            try:
                made = privacy.make_request(request.user, chosen, details,
                                            organization=membership.organization if membership else None)
            except (ValidationFailure, PermissionFailure) as exc:
                error = exc.message
            else:
                messages.success(request, f"Request {made.reference} sent. We'll reply by {made.reply_by:%d %b %Y}.")
                return redirect(reverse("security-home") + "#privacy")
    return render(request, "banda/app/data_request.html", {
        "kinds": kinds, "chosen": chosen, "details": details, "error": error,
        "needs_details": sorted(privacy.NEEDS_DETAILS), "dpo_email": settings.DPO_EMAIL,
    }, status=400 if error else 200)


@login_required
@require_GET
def business_activity_view(request):
    """Owners: what happened on the business account, including B & A staff opening requests."""
    from apps.audit.activity import business_activity

    membership = current_membership(request) if request.user.is_client_account else None
    if membership is None or not membership.is_owner:
        raise Http404
    return render(request, "banda/app/activity.html", {
        "organization": membership.organization,
        "rows": business_activity(membership.organization, request.user),
    })


@login_required
@require_POST
def security_details(request):
    form = DetailsForm(request.POST)
    membership = current_membership(request) if request.user.is_client_account else None
    if not form.is_valid():
        messages.error(request, "Enter your full name.")
        return redirect("security-home")
    business = form.cleaned_data["business_name"] if membership is not None and membership.is_owner else None
    try:
        changed = profile.update_details(request.user, full_name=form.cleaned_data["full_name"],
                                         membership=membership, business_name=business)
    except (ValidationFailure, PermissionFailure) as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "Details saved." if changed else "Nothing to change.")
    return redirect(reverse("security-home") + "#details")


@login_required
@require_POST
def security_preferences(request):
    if not request.user.is_client_account:
        raise Http404
    form = ContactPreferencesForm(request.POST)
    form.is_valid()
    membership = current_membership(request)
    profile.set_contact_preferences(
        request.user,
        updates_by_email=bool(form.cleaned_data.get("updates_by_email")),
        marketing_by_email=bool(form.cleaned_data.get("marketing_by_email")),
        organization=membership.organization if membership else None,
    )
    messages.success(request, "Contact preferences saved.")
    return redirect(reverse("security-home") + "#contact")


@login_required
@require_http_methods(["GET", "POST"])
def security_email(request):
    """Change email: a code to the new address, then a notice to the old one."""
    if (stop := _needs_step_up(request, reverse("security-email"))) is not None:
        return stop
    user = request.user
    pending = request.session.get("pending_email_change")
    form = VerifyForm(request.POST or None) if pending and request.POST.get("code") is not None else None
    if request.method == "POST" and request.POST.get("email") is not None:
        try:
            to = codes.start_email_change(user, request.POST["email"], client_ip=_client_ip(request))
        except (ValidationFailure, RateLimited) as exc:
            return render(request, "banda/app/change_contact.html", {
                "kind": "email", "value": request.POST.get("email", ""), "error": exc.message})
        request.session["pending_email_change"] = {"value": request.POST["email"].strip().lower(), "to": to}
        return redirect("security-email")
    if form is not None and form.is_valid():
        try:
            user = codes.confirm_email_change(user, pending["value"], form.cleaned_data["code"])
        except (ValidationFailure, RateLimited) as exc:
            form.add_error("code", exc.message)
        else:
            request.session.pop("pending_email_change", None)
            _keep_this_session(request, user)
            messages.success(request, "Your email address is changed. We've told your old address, and signed out your other devices.")
            return redirect("security-home")
    if request.GET.get("restart"):
        request.session.pop("pending_email_change", None)
        return redirect("security-email")
    return render(request, "banda/app/change_contact.html", {
        "kind": "email", "pending": request.session.get("pending_email_change"), "form": form or VerifyForm()})


@login_required
@require_http_methods(["GET", "POST"])
def security_phone(request):
    """Add or change the phone number: a code to the new number, then a notice to the old one."""
    if (stop := _needs_step_up(request, reverse("security-phone"))) is not None:
        return stop
    user = request.user
    pending = request.session.get("pending_phone_change")
    form = VerifyForm(request.POST or None) if pending and request.POST.get("code") is not None else None
    channels = [(c, "SMS" if c == "sms" else CHANNEL_LABELS[c]) for c in codes.phone_channels()]
    if request.method == "POST" and request.POST.get("phone") is not None:
        try:
            to = codes.start_phone_change(user, request.POST["phone"], channel=request.POST.get("channel", ""),
                                          client_ip=_client_ip(request))
        except (ValidationFailure, RateLimited) as exc:
            return render(request, "banda/app/change_contact.html", {
                "kind": "phone", "value": request.POST.get("phone", ""), "error": exc.message, "channels": channels,
                "phone_ok": bool(channels)})
        request.session["pending_phone_change"] = {"value": phones.normalise(request.POST["phone"]), "to": to}
        return redirect("security-phone")
    if form is not None and form.is_valid():
        try:
            user = codes.confirm_phone_change(user, pending["value"], form.cleaned_data["code"])
        except (ValidationFailure, RateLimited) as exc:
            form.add_error("code", exc.message)
        else:
            request.session.pop("pending_phone_change", None)
            _keep_this_session(request, user)
            messages.success(request, "Your phone number is confirmed. You can log in with it now.")
            return redirect("security-home")
    if request.GET.get("restart"):
        request.session.pop("pending_phone_change", None)
        return redirect("security-phone")
    return render(request, "banda/app/change_contact.html", {
        "kind": "phone", "pending": request.session.get("pending_phone_change"), "form": form or VerifyForm(),
        "channels": channels, "phone_ok": bool(channels)})


@login_required
@require_POST
def security_phone_remove(request):
    if (stop := _needs_step_up(request)) is not None:
        return stop
    try:
        user = codes.remove_phone(request.user)
    except ValidationFailure as exc:
        messages.error(request, exc.message)
    else:
        _keep_this_session(request, user)
        messages.success(request, "Your phone number is removed. Codes now come by email.")
    return redirect("security-home")


@login_required
@require_POST
def security_mfa_off(request):
    """Clients can turn their optional authenticator off with a code from it."""
    if (stop := _needs_step_up(request, reverse("security-mfa"))) is not None:
        return stop
    user = request.user
    if not services.verify_mfa(user, request.POST.get("code", "")):
        messages.error(request, "That authenticator code isn't right. Try the newest code in the app.")
        return redirect("security-mfa")
    try:
        user = codes.disable_mfa(user)
    except ValidationFailure as exc:
        messages.error(request, exc.message)
        return redirect("security-home")
    _keep_this_session(request, user)
    messages.success(request, "The authenticator app is turned off. You'll log in with your code only.")
    return redirect("security-home")


@login_required
@require_POST
def security_sessions_others(request):
    count = services.revoke_other_sessions(request.user, request.session.session_key)
    messages.success(request, f"Signed out of {count} other device{'' if count == 1 else 's'}." if count else "No other devices were signed in.")
    return redirect("security-home")


@login_required
@require_POST
def security_channel(request):
    form = ChannelForm(request.POST, channels=codes.channels_for(request.user))
    if form.is_valid():
        codes.set_channel(request.user, form.cleaned_data["channel"])
        messages.success(request, f"We'll send your codes by {CHANNEL_IN_TEXT[form.cleaned_data['channel']]}.")
    else:
        messages.error(request, "Choose a way we can reach you.")
    return redirect("security-home")


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
            audit_user = User.objects.get(pk=user.pk)
            codes._notify_email(audit_user.email, "Authenticator app turned on",
                                f"An authenticator app was set up for your B & A Essentials account. {codes._not_you()}")
            messages.success(request, "The authenticator app is on. You'll enter a code from it each time you log in.")
            return redirect("security-home")
    elif request.method == "GET" and not user.mfa_enabled:
        # Keep the key already shown until set-up is finished. Making a new one on
        # every visit meant any background request (a browser extension, a favicon
        # redirected here for staff without an authenticator) silently replaced the
        # key the person was typing into their app. "Start again" asks for a new one.
        secret = None if request.GET.get("new") else _enrollment_secret(user)
        if not secret:
            secret = services.begin_mfa_enrollment(user)

    uri = None
    if secret:
        from . import mfa as mfa_helpers

        uri = mfa_helpers.provisioning_uri(secret, email=user.email)
    return render(
        request,
        "banda/app/mfa.html",
        {"form": form, "secret": secret, "uri": uri, "enabled": user.mfa_enabled,
         "can_turn_off": user.is_client_account},
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
    services.revoke_session(request.user, session_key)
    if session_key == request.session.session_key:
        services.end_session(request)
        return redirect("login")
    messages.success(request, "That session was signed out.")
    return redirect("security-home")
