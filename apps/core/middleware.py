from __future__ import annotations

import re
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .context import new_correlation_id, set_correlation_id

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

PUBLIC_PREFIXES = (
    "/static/",
    "/health/",
    "/login",
    "/signup",
    "/verify",
    "/password-reset",
    "/invite/",
)


def _is_public(path: str) -> bool:
    if path == "/":
        return True
    return path.startswith((*PUBLIC_PREFIXES, f"/{settings.ADMIN_URL}login"))


def is_safe_redirect(request, url: str) -> bool:
    """Only addresses on this site: a path, or a full URL with this host. Never `//other` or another scheme."""
    return bool(url) and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )


def _safe_next(request, fallback: str = "/app/") -> str:
    """Where to go next: the `next` parameter, else `fallback`, but only if it stays on this site.

    The fallback is checked too: callers pass addresses that came from a link (kept in the
    session during sign-in, or the step-up `next`), and those must never lead off-site.
    """
    for candidate in (request.GET.get("next") or request.POST.get("next"), fallback):
        if candidate and is_safe_redirect(request, candidate):
            return candidate
    return "/app/"


class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inbound = request.headers.get("X-Request-ID", "")
        request_id = inbound if REQUEST_ID_RE.match(inbound) else new_correlation_id()
        request.request_id = request_id
        set_correlation_id(request_id)
        from .client_ip import client_ip
        from .context import reset_client_ip, set_client_ip

        token = set_client_ip(client_ip(request))
        try:
            response = self.get_response(request)
        finally:
            reset_client_ip(token)
        response["X-Request-ID"] = request_id
        return response

    def process_exception(self, request, exception):
        return None


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", settings.CONTENT_SECURITY_POLICY)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if request.path.startswith(("/app/", "/staff/")):
            response.setdefault("Cache-Control", "no-store")
            response.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response


class SessionValidationMiddleware:
    """Enforces credential-version binding, idle expiry and verified email."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        session = request.session
        if session.get("cv") != user.credential_version:
            logout(request)
            if _is_public(request.path):
                messages.info(request, "Your session ended. Please sign in again.")
                return redirect(_safe_next(request, reverse("login")))
            return self._expired(request)

        now = int(time.time())
        last_seen = session.get("last_seen")
        # "Keep me signed in" (clients only) allows a longer quiet spell; see settings.
        idle_limit = settings.REMEMBER_ME_IDLE_TIMEOUT if session.get("remember") else settings.SESSION_IDLE_TIMEOUT
        if last_seen and now - int(last_seen) > idle_limit:
            logout(request)
            return self._expired(request)

        if now - int(last_seen or 0) > 300:
            session["last_seen"] = now

        if (user.email_verified_at is None and request.path.startswith(("/app/", "/staff/"))
                and not request.path.startswith(("/app/security/mfa", "/logout"))):
            messages.info(request, "Verify your email address to continue.")
            return redirect("verify-email")

        return self.get_response(request)

    def _expired(self, request):
        message = "Your session expired. Please sign in again."
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=401)
            response["HX-Redirect"] = f"{reverse('login')}?next={request.path}"
            return response
        messages.info(request, message)
        return redirect(f"{reverse('login')}?next={request.path}")
