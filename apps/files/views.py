from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from apps.audit import services as audit_service
from apps.core import client_status
from apps.organizations.access import client_required

from .models import PrivateFile, ScanStatus
from .policy import can_view_file, token_matches
from .storage import get_storage


@client_required
@require_GET
def files_home(request):
    """Deliverables & Files: delivered work and the files the business uploaded, in one list."""
    from django.core.paginator import Paginator

    from apps.reviews.models import DeliverableVersion, DeliverableVersionStatus
    from apps.tasks.models import TaskAttachment, TaskStatus

    organization = request.organization
    delivered = (DeliverableVersion.objects.filter(deliverable__task__organization=organization,
                                                    status=DeliverableVersionStatus.RELEASED)
                 .select_related("file", "deliverable__task__service_version__definition__category"))
    uploads = (TaskAttachment.objects.filter(task__organization=organization)
               .select_related("file", "task__service_version__definition__category"))
    rows = [_file_row(v.file, v.deliverable.task, "delivered", v.released_at or v.created_at,
                      waiting=v.deliverable.task.status == TaskStatus.DELIVERED, version=v.version_number)
            for v in delivered]
    rows += [_file_row(a.file, a.task, "uploaded", a.created_at) for a in uploads]
    rows.sort(key=lambda r: r["added"], reverse=True)

    counts = {
        "all": len(rows),
        "delivered": sum(r["kind"] == "delivered" for r in rows),
        "waiting": sum(r["waiting"] for r in rows),
        "uploaded": sum(r["kind"] == "uploaded" for r in rows),
    }
    categories = sorted({r["category"] for r in rows if r["category"]})
    kind = request.GET.get("kind", "")
    category = request.GET.get("category", "")
    ftype = request.GET.get("type", "")
    query = (request.GET.get("q") or "").strip()[:100].lower()
    if kind == "waiting":
        rows = [r for r in rows if r["waiting"]]
    elif kind in ("delivered", "uploaded"):
        rows = [r for r in rows if r["kind"] == kind]
    else:
        kind = ""
    if category:
        rows = [r for r in rows if r["category"] == category]
    if ftype in FILE_TYPES:
        rows = [r for r in rows if r["type"] == ftype]
    else:
        ftype = ""
    if query:
        rows = [r for r in rows if query in r["file"].original_name.lower()
                or query in r["task"].reference.lower() or query in r["task"].title.lower()]
    page = Paginator(rows, 10).get_page(request.GET.get("page"))
    keep = request.GET.copy()
    keep.pop("page", None)
    return render(request, "banda/app/files.html", {
        "page": page, "counts": counts, "categories": categories, "file_types": FILE_TYPES,
        "kind": kind, "category": category, "ftype": ftype, "query": request.GET.get("q", "").strip()[:100],
        "filtered": bool(kind or category or ftype or query), "keep": keep.urlencode(),
        "max_mb": settings.FILE_MAX_BYTES // (1024 * 1024),
    })


FILE_TYPES = {"pdf": "PDF", "doc": "Document", "sheet": "Spreadsheet", "image": "Image", "other": "Other"}
EXTENSIONS = {"pdf": "pdf", "doc": "doc", "docx": "doc", "txt": "doc", "xlsx": "sheet", "csv": "sheet",
              "jpg": "image", "jpeg": "image", "png": "image"}
TYPE_ICONS = {"pdf": "file-text", "doc": "file-pen-line", "sheet": "file-spreadsheet", "image": "files", "other": "files"}
TYPE_TONES = client_status.FILE_TONES


def _file_row(private_file, task, kind, added, *, waiting=False, version=None):
    ext = private_file.original_name.rsplit(".", 1)[-1].lower() if "." in private_file.original_name else ""
    ftype = EXTENSIONS.get(ext, "other")
    if private_file.purged_at:
        state = "deleted"
    elif private_file.scan_status == ScanStatus.PENDING:
        state = "checking"
    elif private_file.scan_status != ScanStatus.CLEAN:
        state = "blocked"
    elif waiting:
        state = "waiting"
    else:
        state = "ready" if kind == "delivered" else "uploaded"
    status = client_status.FILE_STATUS[state]
    return {
        "file": private_file, "task": task, "kind": kind, "added": added, "waiting": waiting, "version": version,
        "type": ftype, "type_label": FILE_TYPES[ftype], "tone": TYPE_TONES[ftype], "icon": TYPE_ICONS[ftype], "status": status,
        "category": task.service_version.definition.category.name,
        "downloadable": private_file.is_downloadable and not private_file.purged_at,
    }


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
