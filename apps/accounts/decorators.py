from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.exceptions import PermissionFailure

from . import services
from .permissions import has_perm


def staff_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not request.user.staff_role:
            messages.error(request, "Staff access only.")
            return redirect("client-overview")
        if not request.user.email_verified:
            return redirect("verify-email")
        return view(request, *args, **kwargs)

    return wrapper


def staff_permission_required(code: str):
    def decorator(view):
        @staff_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not has_perm(request.user, code):
                messages.error(request, "You do not have permission to do that.")
                return redirect("staff-dashboard")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def fresh_step_up_required(view):
    """Require a recent password (+ TOTP) confirmation before a sensitive action."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and not services.has_fresh_step_up(
            request, request.user
        ):
            messages.warning(request, "Confirm your identity to continue.")
            return redirect(f"{reverse('step-up')}?next={request.path}")
        return view(request, *args, **kwargs)

    return wrapper
