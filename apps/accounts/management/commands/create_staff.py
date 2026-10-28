from __future__ import annotations

import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import StaffRole, User


class Command(BaseCommand):
    help = "Create (or promote) a staff account. MFA is still required at first login."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--role", required=True, choices=StaffRole.values)
        parser.add_argument("--name", default="", help="Full name")
        parser.add_argument(
            "--admin",
            action="store_true",
            help="Also grant access to the Django admin site",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Update the role of an existing account instead of failing",
        )
        parser.add_argument(
            "--noinput",
            action="store_true",
            help="Read the password from BA_STAFF_PASSWORD instead of prompting",
        )

    def handle(self, *args, **options):
        email = (options["email"] or "").strip().lower()
        if not email:
            raise CommandError("An email address is required.")

        existing = User.objects.filter(email=email).first()
        if existing is not None:
            if not options["update"]:
                raise CommandError(
                    "An account with that email already exists. Use --update to change its role."
                )
            existing.staff_role = options["role"]
            existing.is_staff = existing.is_staff or options["admin"]
            existing.save(update_fields=["staff_role", "is_staff", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(f"Updated {email} to role {options['role']}.")
            )
            return

        password = self._password(options["noinput"])
        user = User(
            email=email,
            full_name=(options["name"] or "").strip(),
            staff_role=options["role"],
            is_staff=options["admin"],
        )
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc

        user.set_password(password)
        user.email_verified_at = timezone.now()
        user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {email} with role {options['role']}. "
                "The account must enrol two-factor authentication at first login."
            )
        )

    def _password(self, noinput: bool) -> str:
        if noinput:
            password = os.environ.get("BA_STAFF_PASSWORD", "")
            if not password:
                raise CommandError(
                    "Set BA_STAFF_PASSWORD when using --noinput."
                )
            return password

        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise CommandError("Passwords do not match.")
        return password
