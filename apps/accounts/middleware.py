from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin

ALLOWED_PATHS = (
    "/app/security/mfa",
    "/app/security/",  # profile pages stay reachable so MFA can be enabled
    "/step-up/",
    "/logout/",
    "/login",
    "/static/",
    "/health/",
    "/admin/",
)


class StaffMFAEnforcementMiddleware(MiddlewareMixin):
    """Staff accounts must enrol a second factor before using operations.

    Enforced server-side on every request, not by hiding navigation.
    """

    def process_request(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        if not getattr(user, "staff_role", ""):
            return None
        if user.mfa_enabled:
            return None
        if any(request.path.startswith(prefix) for prefix in ALLOWED_PATHS):
            return None
        messages.warning(
            request, "Staff accounts must enable two-factor authentication to continue."
        )
        return redirect(f"{reverse('security-mfa')}")
