from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from apps.billing.models import UsageLedger
from apps.files import services as file_services
from apps.files.models import FilePurpose
from apps.reviews.models import Deliverable, DeliverableVersion, DeliverableVersionStatus

from .conftest import pdf_upload


def test_catalogue_lists_open_services_and_prefills_the_request_form(
    client, service_version
):
    response = client.get(reverse("service-catalogue"))

    assert response.status_code == 200
    assert "Logo Refresh" in response.content.decode()

    form_page = client.get(reverse("task-submit"), {"service": str(service_version.pk)})
    assert str(form_page.context["form"]["service_version"].value()) == str(
        service_version.pk
    )


def test_files_page_shows_only_released_own_tenant_deliverables(
    client,
    owner,
    organization,
    submitted_task,
    make_task,
    other_owner,
    other_organization,
):
    released_file = file_services.store_upload(
        organization=organization,
        user=owner,
        uploaded_file=pdf_upload("released-synthetic.pdf"),
        purpose=FilePurpose.DELIVERABLE,
    )
    deliverable = Deliverable.objects.create(task=submitted_task)
    DeliverableVersion.objects.create(
        deliverable=deliverable,
        version_number=1,
        file=released_file,
        submitted_by=owner,
        status=DeliverableVersionStatus.RELEASED,
        released_at=timezone.now(),
    )
    draft_file = file_services.store_upload(
        organization=organization,
        user=owner,
        uploaded_file=pdf_upload("draft-synthetic.pdf"),
        purpose=FilePurpose.DELIVERABLE,
    )
    DeliverableVersion.objects.create(
        deliverable=deliverable,
        version_number=2,
        file=draft_file,
        submitted_by=owner,
        status=DeliverableVersionStatus.PENDING_REVIEW,
    )

    other_task = make_task(other_organization, other_owner)
    other_file = file_services.store_upload(
        organization=other_organization,
        user=other_owner,
        uploaded_file=pdf_upload("other-tenant-synthetic.pdf"),
        purpose=FilePurpose.DELIVERABLE,
    )
    other_deliverable = Deliverable.objects.create(task=other_task)
    DeliverableVersion.objects.create(
        deliverable=other_deliverable,
        version_number=1,
        file=other_file,
        submitted_by=other_owner,
        status=DeliverableVersionStatus.RELEASED,
        released_at=timezone.now(),
    )

    body = client.get(reverse("files-home")).content.decode()

    assert "released-synthetic.pdf" in body
    assert "draft-synthetic.pdf" not in body
    assert "other-tenant-synthetic.pdf" not in body


def test_plan_page_shows_subscription_and_usage(
    client, subscription, submitted_task, organization
):
    UsageLedger.objects.create(
        organization=organization,
        task=submitted_task,
        period_start=subscription.period_start,
        units=1,
    )

    response = client.get(reverse("plan-home"))
    body = response.content.decode()

    assert response.status_code == 200
    assert subscription.plan.name in body
    assert submitted_task.reference in body
    assert "1" in body
