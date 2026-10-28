from __future__ import annotations

import uuid

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import StaffRole
from apps.quotes import services as quote_services
from apps.tasks.models import TaskStatus

from .conftest import login_client, make_user


def test_public_pages_render(db):
    public = Client()

    for name in ("landing", "signup", "login", "password-reset"):
        response = public.get(reverse(name))
        assert response.status_code == 200, name


def test_anonymous_private_urls_redirect_to_login():
    public = Client()

    for name in ("client-overview", "task-list", "billing-home", "staff-dashboard"):
        response = public.get(reverse(name))
        assert response.status_code == 302, name
        assert response["Location"].startswith("/login"), name


def test_client_pages_render(client, submitted_task):
    for name in ("client-overview", "task-list", "task-submit", "billing-home", "team-home", "security-home"):
        response = client.get(reverse(name))
        assert response.status_code == 200, name

    detail = client.get(reverse("task-detail", args=[submitted_task.pk]))
    assert detail.status_code == 200


def test_client_task_detail_with_quote_and_invoice(
    client, scoping_task, quote_approver, owner
):
    version = quote_services.create_version(
        scoping_task,
        quote_approver,
        scope_summary="Refresh the logo for print and web.",
        exclusions="",
        amount_minor=250_000,
        turnaround_days=5,
        revision_allowance=1,
    )
    quote_services.send_version(scoping_task, version, quote_approver)

    response = client.get(reverse("task-detail", args=[scoping_task.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "NGN" in body
    assert "Accept" in body


def test_staff_pages_render_for_operations(staff_client, submitted_task):
    for name in ("staff-dashboard", "staff-queue", "staff-clients"):
        response = staff_client.get(reverse(name))
        assert response.status_code == 200, name

    detail = staff_client.get(reverse("staff-task-detail", args=[submitted_task.pk]))
    assert detail.status_code == 200


def test_finance_and_review_pages_render(db, finance, reviewer):
    finance_client = login_client(finance)
    assert finance_client.get(reverse("staff-finance")).status_code == 200

    reviewer_client = login_client(reviewer)
    assert reviewer_client.get(reverse("staff-reviews")).status_code == 200


def test_catalogue_page_renders_for_administrator(db):
    admin = make_user("admin@example.test", staff_role=StaffRole.ADMIN, mfa=True)
    admin_client = login_client(admin)

    response = admin_client.get(reverse("staff-catalogue"))

    assert response.status_code == 200


def test_client_cannot_reach_finance_or_catalogue(client):
    assert client.get(reverse("staff-finance")).status_code == 302
    assert client.get(reverse("staff-catalogue")).status_code == 302


def test_missing_task_renders_404_page(client):
    response = client.get(reverse("task-detail", args=[uuid.uuid4()]))

    assert response.status_code == 404
    assert b"not found" in response.content.lower()


def test_staff_task_detail_for_every_stage_renders(
    staff_client, submitted_task, ops_user
):
    for status in (
        TaskStatus.SUBMITTED,
        TaskStatus.TRIAGE,
        TaskStatus.SCOPING,
        TaskStatus.AWAITING_PAYMENT,
        TaskStatus.READY_FOR_ASSIGNMENT,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.INTERNAL_REVIEW,
        TaskStatus.ON_HOLD,
    ):
        from apps.tasks.models import Task

        Task.objects.filter(pk=submitted_task.pk).update(status=status)

        response = staff_client.get(
            reverse("staff-task-detail", args=[submitted_task.pk])
        )
        assert response.status_code == 200, status
