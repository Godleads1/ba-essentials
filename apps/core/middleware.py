from __future__ import annotations

import hashlib
import re
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .context import get_correlation_id, new_correlation_id, set_correlation_id

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

PUBLIC_PREFIXES = (
    "/static/",
    "/health/",
    "/login",
    "/signup",
    "/verify",
    "/password-reset",
    "/invite/",
    "/admin/login",
)


def _is_public(path: str) -> bool:
    if path == "/":
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _safe_next(request, fallback: str = "/app/") -> str:
    candidate = request.GET.get("next") or request.POST.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback


class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inbound = request.headers.get("X-Request-ID", "")
        request_id = inbound if REQUEST_ID_RE.match(inbound) else new_correlation_id()
        request.request_id = request_id
        set_correlation_id(request_id)
        response = self.get_response(request)
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
        if last_seen and now - int(last_seen) > settings.SESSION_IDLE_TIMEOUT:
            logout(request)
            return self._expired(request)

        if now - int(last_seen or 0) > 300:
            session["last_seen"] = now

        if user.email_verified_at is None and request.path.startswith(("/app/", "/staff/")):
            if not request.path.startswith(("/app/security/mfa", "/logout")):
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
