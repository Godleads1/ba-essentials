from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from .conftest import login_client

from apps.files import services as file_services
from apps.quotes import services as quote_services
from apps.tasks.access import tasks_for
from apps.tasks.models import Assignment, AssignmentStatus, TaskAttachment

from .conftest import pdf_upload


def test_task_querysets_never_cross_tenants(
    submitted_task, other_owner, organization, other_organization
):
    visible_to_other = tasks_for(other_owner)

    assert not visible_to_other.filter(pk=submitted_task.pk).exists()
    assert tasks_for(other_owner).count() == 0
    assert tasks_for(submitted_task.created_by).filter(pk=submitted_task.pk).exists()


def test_client_cannot_open_another_tenants_task(client, other_client, submitted_task):
    response = other_client.get(reverse("task-detail", args=[submitted_task.pk]))

    assert response.status_code == 404


def test_client_cannot_accept_another_tenants_quote(
    scoping_task, quote_approver, other_client
):
    version = quote_services.create_version(
        scoping_task,
        quote_approver,
        scope_summary="Scope",
        exclusions="",
        amount_minor=1000,
        turnaround_days=2,
        revision_allowance=0,
    )
    quote_services.send_version(scoping_task, version, quote_approver)

    response = other_client.post(reverse("quote-accept", args=[version.pk]))

    assert response.status_code == 404


def test_specialist_only_sees_assigned_tasks(
    submitted_task, specialist, ops_user, organization, owner
):
    assert tasks_for(specialist).count() == 0

    Assignment.objects.create(
        task=submitted_task,
        specialist=specialist,
        assigned_by=ops_user,
        status=AssignmentStatus.ACTIVE,
    )

    assert tasks_for(specialist).filter(pk=submitted_task.pk).exists()
    assert tasks_for(specialist).count() == 1


def test_specialist_cannot_reach_unassigned_staff_task(
    submitted_task, specialist
):
    staff = login_client(specialist)

    response = staff.get(reverse("staff-task-detail", args=[submitted_task.pk]))

    assert response.status_code == 404


def test_client_cannot_download_another_tenants_attachment(
    other_client, organization, owner, submitted_task
):
    private = file_services.store_upload(
        organization=organization, user=owner, uploaded_file=pdf_upload()
    )
    TaskAttachment.objects.create(task=submitted_task, file=private, added_by=owner)

    response = other_client.get(reverse("file-download", args=[private.pk]))

    assert response.status_code == 403


def test_client_cannot_reach_staff_application(client):
    response = client.get(reverse("staff-dashboard"))

    assert response.status_code == 302
    assert response["Location"] == reverse("client-overview")


def test_anonymous_staff_request_is_sent_to_login():
    response = Client().get(reverse("staff-dashboard"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))
