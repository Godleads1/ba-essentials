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
        parser.add_argument("--role", choices=StaffRole.values, help="Required unless --superuser")
        parser.add_argument("--name", default="", help="Full name")
        parser.add_argument("--phone", default="", help="Phone number, for example 0803 123 4567 (contact only)")
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="A superuser: the Admin role, Django admin and every permission",
        )
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
        if options["superuser"]:
            options["role"], options["admin"] = StaffRole.ADMIN, True
        if not options["role"]:
            raise CommandError("Choose a --role (or use --superuser).")
        phone = ""
        if options["phone"].strip():
            from apps.accounts import phones
            from apps.core.exceptions import ValidationFailure

            try:
                phone = phones.normalise(options["phone"])
            except ValidationFailure as exc:
                raise CommandError(exc.message) from exc

        existing = User.objects.filter(email=email).first()
        if existing is not None:
            if not options["update"]:
                raise CommandError(
                    "An account with that email already exists. Use --update to change its role."
                )
            from apps.accounts.protection import ProtectedAccount

            existing.staff_role = options["role"]
            existing.is_staff = existing.is_staff or options["admin"]
            existing.is_superuser = existing.is_superuser or options["superuser"]
            fields = ["staff_role", "is_staff", "is_superuser", "updated_at"]
            if options["name"].strip():
                existing.full_name = options["name"].strip()
                fields.append("full_name")
            if phone:
                existing.phone = phone
                fields.append("phone")
            try:
                existing.save(update_fields=fields)
            except ProtectedAccount as exc:
                raise CommandError(exc.message) from exc
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
            is_superuser=options["superuser"],
            phone=phone,
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
                f"Created {email} with role {options['role']}{' (superuser)' if options['superuser'] else ''}. "
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
