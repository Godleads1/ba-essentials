from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from apps.audit import services as audit_service
from apps.organizations.access import client_required

from .models import PrivateFile, ScanStatus
from .policy import can_view_file, token_matches
from .storage import get_storage


@client_required
@require_GET
def files_home(request):
    from apps.reviews.models import DeliverableVersion, DeliverableVersionStatus
    from apps.tasks.models import TaskAttachment

    organization = request.organization
    attachments = (
        TaskAttachment.objects.filter(task__organization=organization)
        .select_related("file", "task")
        .order_by("-created_at")
    )
    deliverables = (
        DeliverableVersion.objects.filter(
            deliverable__task__organization=organization,
            status=DeliverableVersionStatus.RELEASED,
        )
        .select_related("file", "deliverable__task")
        .order_by("-released_at")
    )
    return render(
        request,
        "app/files.html",
        {"attachments": attachments, "deliverables": deliverables},
    )


@login_required
@require_GET
def download(request, file_id):
    private_file = get_object_or_404(PrivateFile, pk=file_id)
    token = request.GET.get("token", "")

    allowed = bool(token) and token_matches(token, private_file, request.user)
    if not allowed:
        allowed = can_view_file(request.user, private_file)

    if not allowed:
        audit_service.record(
            action="file.download_denied",
            actor=request.user,
            organization=private_file.organization,
            resource_type="file",
            resource_id=str(private_file.pk),
            result="denied",
        )
        return render(request, "errors/403.html", {"reason": "You cannot access this file."}, status=403)

    if private_file.scan_status != ScanStatus.CLEAN:
        return render(
            request,
            "errors/409.html",
            {
                "reason": "This file is still being checked and cannot be downloaded yet.",
                "detail": private_file.scan_detail,
            },
            status=409,
        )

    storage = get_storage()
    if not storage.exists(private_file.storage_key):
        messages.error(request, "That file is no longer available.")
        return render(request, "errors/404.html", {}, status=404)

    audit_service.record(
        action="file.downloaded",
        actor=request.user,
        organization=private_file.organization,
        resource_type="file",
        resource_id=str(private_file.pk),
    )
    response = FileResponse(
        storage.open(private_file.storage_key),
        as_attachment=True,
        filename=private_file.original_name,
        content_type=private_file.content_type,
    )
    response["Cache-Control"] = "private, no-store"
    return response
