from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class StaffRole(models.TextChoices):
    OPERATIONS = "operations", "Operations"
    QUOTE_APPROVER = "quote_approver", "Quote Approver"
    SPECIALIST = "specialist", "Specialist"
    REVIEWER = "reviewer", "Reviewer / QA"
    SUPPORT = "support", "Support"
    FINANCE = "finance", "Finance"
    ADMIN = "admin", "Administrator"
    WEBSITE_EDITOR = "website_editor", "Website Editor"
    WEBSITE_PUBLISHER = "website_publisher", "Website Publisher"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _normalize(self, email: str) -> str:
        return (email or "").strip().lower()

    def create_user(self, email: str, password: str | None = None, **extra):
        if not email:
            raise ValueError("An email address is required")
        email = self._normalize(email)
        user = self.model(email=email, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("staff_role", StaffRole.ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("email_verified_at", timezone.now())
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(
        default=False, help_text="Access to the Django admin, not the staff app."
    )
    staff_role = models.CharField(
        max_length=32, choices=StaffRole.choices, blank=True, db_index=True
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)
    credential_version = models.PositiveIntegerField(default=1)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret_encrypted = models.TextField(blank=True)
    mfa_confirmed_at = models.DateTimeField(null=True, blank=True)
    mfa_last_timestep = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email

    @property
    def is_staff_member(self) -> bool:
        return bool(self.staff_role) and self.is_active

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    def mark_email_verified(self) -> None:
        if self.email_verified_at is None:
            self.email_verified_at = timezone.now()
            self.save(update_fields=["email_verified_at", "updated_at"])

    def bump_credential_version(self) -> int:
        User.objects.filter(pk=self.pk).update(
            credential_version=models.F("credential_version") + 1
        )
        self.refresh_from_db(fields=["credential_version"])
        return self.credential_version


class UserSession(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sessions"
    )
    session_key = models.CharField(max_length=64, unique=True)
    user_agent = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [models.Index(fields=["user", "revoked_at"])]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class ChallengePurpose(models.TextChoices):
    EMAIL_VERIFICATION = "email_verification", "Email verification"
    PASSWORD_RESET = "password_reset", "Password reset"


class VerificationChallenge(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="challenges"
    )
    purpose = models.CharField(max_length=32, choices=ChallengePurpose.choices)
    code_hash = models.CharField(max_length=64)
    salt = models.CharField(max_length=32)
    credential_version = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "purpose", "consumed_at"])]

    @property
    def is_usable(self) -> bool:
        return (
            self.consumed_at is None
            and self.superseded_at is None
            and self.expires_at > timezone.now()
            and self.attempts < self.max_attempts
        )


class PasswordHistory(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_history"
    )
    password_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        get_latest_by = "created_at"
