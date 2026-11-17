from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import StaffRole, User
from apps.billing.models import Plan, Subscription, SubscriptionStatus
from apps.catalogue.models import (
    ServiceCategory,
    ServiceDefinition,
    ServiceState,
    ServiceVersion,
)
from apps.organizations.models import Membership, MembershipRole, Organization
from apps.tasks import services as task_services


class Command(BaseCommand):
    help = (
        "Create synthetic demo accounts, requests, files, messages and support requests. "
        "Development only; never "
        "run this against a production database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true", help="Confirm that this is a disposable database"
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo refuses to run unless DEBUG is enabled.")
        if not options["yes"]:
            raise CommandError(
                "Re-run with --yes once you are sure this database contains no real data."
            )

        password = secrets.token_urlsafe(18)
        with transaction.atomic():
            organization = self._organization()
            users = self._users(password)
            self._catalogue()
            self._plan(organization)

            owner = users["owner"]
            if not organization.tasks.exists():
                version = ServiceVersion.objects.filter(is_current=True).first()
                if version is None:
                    raise CommandError("No current service version was created.")
                task_services.create_request(
                    organization=organization,
                    user=owner,
                    service_version=version,
                    title="Synthetic demo request",
                    brief=(
                        "This is synthetic demo data used to exercise the request "
                        "workflow during local development."
                    ),
                    desired_outcome="A demonstration of the triage and scoping flow.",
                )
            self._samples(organization, owner, users["ops"])

        self.stdout.write(self.style.SUCCESS("Synthetic demo data created."))
        self.stdout.write("Sign in with any of these addresses (all @example.test):")
        for label, email in (
            ("client owner", "demo.owner@example.test"),
            ("operations", "demo.ops@example.test"),
            ("quote approver", "demo.quotes@example.test"),
            ("specialist", "demo.specialist@example.test"),
            ("reviewer", "demo.reviewer@example.test"),
            ("finance", "demo.finance@example.test"),
        ):
            self.stdout.write(f"  {label:15} {email}")
        self.stdout.write(f"Password: {password}")
        self.stdout.write(
            "Staff accounts must enrol two-factor authentication at first login."
        )

    def _organization(self) -> Organization:
        organization, _ = Organization.objects.get_or_create(
            slug="demo-studio", defaults={"name": "Demo Studio (synthetic)"}
        )
        return organization

    def _users(self, password: str) -> dict[str, User]:
        specs = {
            "owner": ("demo.owner@example.test", ""),
            "ops": ("demo.ops@example.test", StaffRole.OPERATIONS),
            "quotes": ("demo.quotes@example.test", StaffRole.QUOTE_APPROVER),
            "specialist": ("demo.specialist@example.test", StaffRole.SPECIALIST),
            "reviewer": ("demo.reviewer@example.test", StaffRole.REVIEWER),
            "finance": ("demo.finance@example.test", StaffRole.FINANCE),
        }
        users: dict[str, User] = {}
        for key, (email, role) in specs.items():
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": f"Demo {key.title()}",
                    "staff_role": role,
                    "email_verified_at": timezone.now(),
                },
            )
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
            users[key] = user

        organization = Organization.objects.get(slug="demo-studio")
        Membership.objects.get_or_create(
            organization=organization,
            user=users["owner"],
            defaults={"role": MembershipRole.OWNER},
        )
        return users

    def _catalogue(self) -> None:
        if ServiceVersion.objects.filter(is_current=True).exists():
            return
        demo_services = (
            (
                "Brand & Design (demo)",
                "brand-design-demo",
                "Logo Concept (demo)",
                "logo-concept-demo",
                "A first logo direction for a small business.",
                "Two logo concepts and a single revision round.",
            ),
            (
                "Documents & Admin (demo)",
                "documents-admin-demo",
                "Document Formatting (demo)",
                "document-formatting-demo",
                "Tidy a business document for sharing.",
                "A formatted PDF and the editable source file.",
            ),
        )
        for category_name, category_slug, name, slug, scope, deliverables in demo_services:
            category, _ = ServiceCategory.objects.get_or_create(
                slug=category_slug, defaults={"name": category_name}
            )
            definition, _ = ServiceDefinition.objects.get_or_create(
                slug=slug, defaults={"category": category, "name": name}
            )
            definition.versions.get_or_create(
                version_number=1,
                defaults={
                    "scope_summary": scope,
                    "deliverables_summary": deliverables,
                    "turnaround_days": 5,
                    "revision_allowance": 1,
                    "state": ServiceState.AVAILABLE,
                    "is_current": True,
                },
            )

    def _samples(self, organization: Organization, owner: User, ops: User) -> None:
        """Synthetic files, messages and support requests, so Deliverables, Messages
        and Help & Support have something to show. Added once."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.communications import services as communications
        from apps.files import services as file_services
        from apps.files.models import FilePurpose
        from apps.reviews.models import Deliverable, DeliverableVersion, DeliverableVersionStatus
        from apps.tasks.models import TaskAttachment, TaskStatus

        if TaskAttachment.objects.filter(task__organization=organization).exists():
            return
        if organization.tasks.count() < 2:
            version = ServiceVersion.objects.filter(is_current=True).last()
            task_services.create_request(
                organization=organization, user=owner, service_version=version,
                title="Synthetic quarterly report",
                brief="Synthetic demo data: format a short quarterly report for sharing.",
                desired_outcome="A tidy PDF and the editable file.",
            )
        tasks = list(organization.tasks.order_by("created_at")[:2])

        def pdf(name: str) -> SimpleUploadedFile:
            return SimpleUploadedFile(name, b"%PDF-1.4\n% synthetic demo file\n", content_type="application/pdf")

        for task in tasks:
            upload = file_services.store_upload(organization=organization, user=owner,
                                                uploaded_file=pdf(f"brief-{task.reference}-synthetic.pdf"),
                                                purpose=FilePurpose.ATTACHMENT)
            TaskAttachment.objects.create(task=task, file=upload, added_by=owner)
            communications.post_task_message(task=task, author=owner,
                                             body="Hi team, can you confirm when this will be ready?")
            communications.post_task_message(task=task, author=ops,
                                             body="Thanks, we've got it. We'll confirm the date by tomorrow.")

        delivered = tasks[-1]
        result = file_services.store_upload(organization=organization, user=ops,
                                            uploaded_file=pdf(f"{delivered.reference}-final-synthetic.pdf"),
                                            purpose=FilePurpose.DELIVERABLE)
        deliverable = Deliverable.objects.create(task=delivered)
        DeliverableVersion.objects.create(deliverable=deliverable, version_number=1, file=result, submitted_by=ops,
                                          status=DeliverableVersionStatus.RELEASED, released_at=timezone.now())
        type(delivered).objects.filter(pk=delivered.pk).update(status=TaskStatus.DELIVERED)

        billing = communications.create_ticket(organization=organization, author=owner,
                                               subject="Bank transfer not showing on my invoice",
                                               body="Synthetic demo: I paid by transfer but the invoice shows due.")
        communications.post_ticket_message(ticket=billing, author=ops,
                                           body="Thanks, we've found the transfer and are matching it now.")
        done = communications.create_ticket(organization=organization, author=owner,
                                            subject="How do I add a colleague?",
                                            body="Synthetic demo: I'd like to add my accountant.", priority="low")
        communications.close_ticket_by_client(done, owner)

    def _plan(self, organization: Organization) -> None:
        plan, _ = Plan.objects.get_or_create(
            slug="demo-starter",
            defaults={
                "name": "Demo Starter",
                "monthly_task_allowance": 2,
                "price_minor": 0,
            },
        )
        now = timezone.now()
        Subscription.objects.get_or_create(
            organization=organization,
            defaults={
                "plan": plan,
                "period_start": now,
                "period_end": now + timedelta(days=30),
                "status": SubscriptionStatus.ACTIVE,
            },
        )
