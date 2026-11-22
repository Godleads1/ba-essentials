"""The staff Team screen: who is on the team, inviting people, changing roles, removing access.

Rules (enforced here, on the server, whatever the page shows):
- Only Admins (permission staff.manage) can make changes. Each change needs a fresh
  "confirm it's you" (checked in the views).
- Inviting someone into a powerful role (Admin, Finance, Quote approver), or moving
  anyone into one, waits until a *different* Admin approves. Nobody can approve their
  own request, or a request about themselves.
- Other roles take effect at once. Removing access always takes effect at once: taking
  power away never needs a second person.
- Nobody can change their own role or remove their own access, and the last active
  Admin can't be removed or moved to another role.
- Superusers are managed on the server only (createsuperuser / create_staff), never here.
- An invitation link is random, stored only as a hash, works once and expires after
  INVITE_DAYS. The person sets their own password (the usual rules, including the
  breached-password check), then must set up an authenticator app at first sign-in.
- Every step is in the audit log. Role changes also raise the staff.access_changed alert.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.audit import services as audit_service
from apps.core.exceptions import PermissionFailure, ValidationFailure

from .models import StaffAccessRequest, StaffRequestKind, StaffRequestStatus, StaffRole, User
from .permissions import STAFF_MANAGE, has_perm

logger = logging.getLogger("ba.accounts")

POWERFUL_ROLES = frozenset({StaffRole.ADMIN, StaffRole.FINANCE, StaffRole.QUOTE_APPROVER})
INVITE_DAYS = 7  # an invitation link works for this long
APPROVAL_DAYS = 7  # a request waiting for a second Admin lapses after this
OPEN = (StaffRequestStatus.AWAITING_APPROVAL, StaffRequestStatus.INVITED)

ROLE_SUMMARIES = {
    StaffRole.ADMIN: "Everything, including the Team screen and the audit log",
    StaffRole.FINANCE: "Payments, invoices and paying out third-party fees",
    StaffRole.QUOTE_APPROVER: "Scoping requests, sending quotes and approving fees",
    StaffRole.OPERATIONS: "The request queue, assigning work and client files",
    StaffRole.SPECIALIST: "Only the requests assigned to them",
    StaffRole.REVIEWER: "Checking finished work before it goes to the client",
    StaffRole.SUPPORT: "Support conversations and client details",
    StaffRole.WEBSITE_EDITOR: "Editing the public website (not publishing)",
    StaffRole.WEBSITE_PUBLISHER: "Editing and publishing the public website",
}


def needs_approval(role: str) -> bool:
    return role in POWERFUL_ROLES


def can_manage(user) -> bool:
    return has_perm(user, STAFF_MANAGE)


def _require_manager(actor) -> None:
    if not can_manage(actor):
        raise PermissionFailure("Only Admins can change the team.")


def _role(role: str) -> str:
    if role not in StaffRole.values:
        raise ValidationFailure("Choose a role.")
    return role


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def team_members():
    return User.objects.filter(Q(staff_role__gt="") | Q(is_staff=True) | Q(is_superuser=True)).order_by(
        "-is_active", "email")


def active_admins():
    return User.objects.filter(is_active=True).filter(Q(staff_role=StaffRole.ADMIN) | Q(is_superuser=True))


def open_requests():
    return (StaffAccessRequest.objects.filter(status__in=OPEN, expires_at__gt=timezone.now())
            .select_related("requested_by", "user"))


def _team_url() -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}{reverse('staff-team')}"


def _email(to: list[str], subject: str, body: str) -> None:
    if not to:
        return
    from apps.core.models import OutboxEvent

    OutboxEvent.objects.create(topic=OutboxEvent.Topic.EMAIL, payload={"to": to, "subject": subject, "body": body})


def _ask_for_approval(req: StaffAccessRequest) -> None:
    others = [a.email for a in active_admins().exclude(pk=req.requested_by_id) if a.email]
    label = StaffRole(req.role).label
    what = (f"invite {req.email} as {label}" if req.kind == StaffRequestKind.INVITE
            else f"give {req.email} the {label} role")
    _email(others, f"Approval needed: {what}",
           f"{getattr(req.requested_by, 'email', 'An Admin')} has asked to {what}.\n\n"
           f"{label} is a powerful role, so a second Admin must approve it. Approve or turn it down "
           f"on the Team screen: {_team_url()}\n\nIt lapses after {APPROVAL_DAYS} days if nobody acts. "
           "If you don't recognise this, turn it down and check the audit log.")


def _audit(action: str, actor, req: StaffAccessRequest, **metadata) -> None:
    audit_service.record(action=action, actor=actor, resource_type="staff_request", resource_id=str(req.pk),
                         metadata={"email": req.email, "role": req.role, **metadata})


# ---------- inviting ----------

def invite(actor, email: str, role: str) -> StaffAccessRequest:
    """Invite someone to the team. Powerful roles wait for a second Admin; others are sent at once."""
    _require_manager(actor)
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValidationFailure("Enter a valid email address.") from exc
    role = _role(role)
    existing = User.objects.filter(email=email).first()
    if existing is not None:
        if existing.is_client_account:
            raise ValidationFailure("That email already has a client account. Staff need their own work email.")
        raise ValidationFailure("That person is already on the team. Change their role instead.")
    now = timezone.now()
    with transaction.atomic():
        StaffAccessRequest.objects.filter(email=email, kind=StaffRequestKind.INVITE, status__in=OPEN).update(
            status=StaffRequestStatus.CANCELLED, decided_at=now)
        req = StaffAccessRequest.objects.create(
            kind=StaffRequestKind.INVITE, email=email, role=role, requested_by=actor,
            status=StaffRequestStatus.AWAITING_APPROVAL if needs_approval(role) else StaffRequestStatus.INVITED,
            expires_at=now + timedelta(days=APPROVAL_DAYS if needs_approval(role) else INVITE_DAYS),
        )
        _audit("staff.invite_requested", actor, req, needs_approval=needs_approval(role))
        if needs_approval(role):
            _ask_for_approval(req)
        else:
            _send_invitation(req)
    return req


def _send_invitation(req: StaffAccessRequest) -> None:
    token = secrets.token_urlsafe(32)
    req.token_hash = _hash(token)
    req.status = StaffRequestStatus.INVITED
    req.expires_at = timezone.now() + timedelta(days=INVITE_DAYS)
    req.save(update_fields=["token_hash", "status", "expires_at"])
    link = f"{settings.APP_BASE_URL.rstrip('/')}{reverse('staff-join', kwargs={'token': token})}"
    _email([req.email], "You've been invited to the B & A Essentials staff team",
           f"You've been invited to join the B & A Essentials staff team as {StaffRole(req.role).label}.\n\n"
           f"Set your password here: {link}\n\n"
           f"The link works once and expires in {INVITE_DAYS} days. When you first sign in you'll set up a "
           "free authenticator app (Google Authenticator or Microsoft Authenticator).\n\n"
           "If you weren't expecting this, ignore this email.")
    _audit("staff.invitation_sent", None, req)


def invitation_for(token: str) -> StaffAccessRequest | None:
    req = StaffAccessRequest.objects.filter(token_hash=_hash(token or ""), kind=StaffRequestKind.INVITE).first()
    if req is None or req.status != StaffRequestStatus.INVITED or req.expired:
        return None
    return req


def accept_invitation(token: str, *, full_name: str, password: str) -> User:
    full_name = " ".join((full_name or "").split())[:150]
    if not full_name:
        raise ValidationFailure("Enter your name.")
    with transaction.atomic():
        req = (StaffAccessRequest.objects.select_for_update()
               .filter(token_hash=_hash(token or ""), kind=StaffRequestKind.INVITE).first())
        if req is None or req.status != StaffRequestStatus.INVITED or req.expired:
            raise ValidationFailure("This invitation link has expired or was already used. Ask an Admin for a new one.")
        candidate = User(email=req.email, full_name=full_name, staff_role=req.role)
        try:
            validate_password(password, user=candidate)
        except ValidationError as exc:
            raise ValidationFailure(" ".join(exc.messages)) from exc
        try:
            with transaction.atomic():
                user = User.objects.create_user(email=req.email, password=password, full_name=full_name,
                                                staff_role=req.role, email_verified_at=timezone.now())
        except IntegrityError as exc:
            raise ValidationFailure("An account with this email already exists. Ask an Admin for help.") from exc
        req.user, req.status, req.completed_at, req.token_hash = user, StaffRequestStatus.DONE, timezone.now(), None
        req.save(update_fields=["user", "status", "completed_at", "token_hash"])
        _audit("staff.invitation_accepted", user, req)
    return user


# ---------- approving ----------

def _open_request(pk) -> StaffAccessRequest:
    req = StaffAccessRequest.objects.select_for_update().filter(pk=pk).first()
    if req is None or req.status not in OPEN or req.expired:
        raise ValidationFailure("That request has already been dealt with or has lapsed.")
    return req


def approve(actor, pk) -> StaffAccessRequest:
    _require_manager(actor)
    with transaction.atomic():
        req = _open_request(pk)
        if req.status != StaffRequestStatus.AWAITING_APPROVAL:
            raise ValidationFailure("That request doesn't need approval.")
        if req.requested_by_id == actor.pk:
            raise PermissionFailure("A different Admin must approve your request.")
        if req.user_id == actor.pk or req.email == actor.email:
            raise PermissionFailure("You can't approve a change to your own access.")
        req.decided_by, req.decided_at = actor, timezone.now()
        req.save(update_fields=["decided_by", "decided_at"])
        _audit("staff.request_approved", actor, req)
        if req.kind == StaffRequestKind.INVITE:
            _send_invitation(req)
        else:
            _apply(req, actor)
    return req


def reject(actor, pk) -> StaffAccessRequest:
    """Turn down (another Admin) or cancel (the person who asked) a waiting request or invitation."""
    _require_manager(actor)
    with transaction.atomic():
        req = _open_request(pk)
        own = req.requested_by_id == actor.pk
        req.status = StaffRequestStatus.CANCELLED if own else StaffRequestStatus.REJECTED
        req.decided_by, req.decided_at, req.token_hash = actor, timezone.now(), None
        req.save(update_fields=["status", "decided_by", "decided_at", "token_hash"])
        _audit("staff.request_cancelled" if own else "staff.request_rejected", actor, req)
    return req


# ---------- changing a team member ----------

def _member(actor, user_id) -> User:
    _require_manager(actor)
    user = team_members().filter(pk=user_id).first()
    if user is None:
        raise ValidationFailure("That person isn't on the team.")
    if user.pk == actor.pk:
        raise PermissionFailure("You can't change your own access. Ask another Admin.")
    if user.is_superuser:
        raise PermissionFailure("Superusers are managed on the server, not here.")
    return user


def _keeps_an_admin(user: User) -> None:
    if user.staff_role == StaffRole.ADMIN and not active_admins().exclude(pk=user.pk).exists():
        raise ValidationFailure("This is the only active Admin. Add another Admin first.")


def change_role(actor, user_id, role: str) -> StaffAccessRequest | None:
    """Returns the waiting request if the new role needs a second Admin, else None (done)."""
    role = _role(role)
    with transaction.atomic():
        user = _member(actor, user_id)
        if user.staff_role == role:
            raise ValidationFailure(f"{user.email} already has that role.")
        if role != StaffRole.ADMIN:
            _keeps_an_admin(user)
        return _request_or_apply(actor, user, StaffRequestKind.ROLE_CHANGE, role)


def restore_access(actor, user_id) -> StaffAccessRequest | None:
    with transaction.atomic():
        user = _member(actor, user_id)
        if user.is_active:
            raise ValidationFailure(f"{user.email} already has access.")
        if not user.staff_role:
            raise ValidationFailure("Give them a role first.")
        return _request_or_apply(actor, user, StaffRequestKind.RESTORE, user.staff_role)


def _request_or_apply(actor, user: User, kind: str, role: str) -> StaffAccessRequest | None:
    now = timezone.now()
    StaffAccessRequest.objects.filter(user=user, status__in=OPEN).update(
        status=StaffRequestStatus.CANCELLED, decided_at=now)
    req = StaffAccessRequest.objects.create(
        kind=kind, email=user.email, user=user, role=role, requested_by=actor,
        status=StaffRequestStatus.AWAITING_APPROVAL, expires_at=now + timedelta(days=APPROVAL_DAYS),
    )
    _audit("staff.change_requested", actor, req, kind=kind, needs_approval=needs_approval(role))
    if needs_approval(role):
        _ask_for_approval(req)
        return req
    _apply(req, actor)
    return None


def _apply(req: StaffAccessRequest, actor) -> None:
    user = User.objects.select_for_update().get(pk=req.user_id)
    if req.role != StaffRole.ADMIN:
        _keeps_an_admin(user)  # checked again: things may have changed while it waited
    user.staff_role, user.is_active = req.role, True
    user.save(update_fields=["staff_role", "is_active", "updated_at"])  # audited and alerted by the signal
    req.status, req.completed_at = StaffRequestStatus.DONE, timezone.now()
    req.save(update_fields=["status", "completed_at"])
    _audit("staff.change_applied", actor, req, kind=req.kind)


def remove_access(actor, user_id) -> User:
    """Takes effect at once and signs them out everywhere. The account is kept for the audit trail."""
    with transaction.atomic():
        user = _member(actor, user_id)
        if not user.is_active:
            raise ValidationFailure(f"{user.email} already has no access.")
        _keeps_an_admin(user)
        user = User.objects.select_for_update().get(pk=user.pk)
        user.is_active = False
        user.save(update_fields=["is_active", "updated_at"])
        user.bump_credential_version()  # every session of theirs ends on its next request
        StaffAccessRequest.objects.filter(user=user, status__in=OPEN).update(
            status=StaffRequestStatus.CANCELLED, decided_at=timezone.now())
        audit_service.record(action="staff.access_removed", actor=actor, resource_type="user",
                             resource_id=str(user.pk), metadata={"email": user.email, "role": user.staff_role})
    return user
