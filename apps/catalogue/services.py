from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from apps.audit import services as audit_service
from apps.core.exceptions import ValidationFailure

from .models import ServiceDefinition, ServiceState, ServiceVersion


def available_versions():
    return (
        ServiceVersion.objects.filter(
            is_current=True,
            state=ServiceState.AVAILABLE,
            definition__is_active=True,
        )
        .select_related("definition", "definition__category")
        .order_by("definition__category__sort_order", "definition__name")
    )


def submittable_versions():
    """Services a client can start a request against, including scoping-only."""
    return (
        ServiceVersion.objects.filter(
            is_current=True,
            state__in=[ServiceState.AVAILABLE, ServiceState.SCOPING_ONLY],
            definition__is_active=True,
        )
        .select_related("definition", "definition__category")
        .order_by("definition__category__sort_order", "definition__name")
    )


@transaction.atomic
def publish_version(
    definition: ServiceDefinition,
    *,
    scope_summary: str,
    deliverables_summary: str,
    turnaround_days: int = 7,
    revision_allowance: int = 1,
    requires_quote: bool = True,
    state: str = ServiceState.SCOPING_ONLY,
    actor=None,
) -> ServiceVersion:
    if not scope_summary.strip() or not deliverables_summary.strip():
        raise ValidationFailure("Scope and deliverables are required to publish a version.")
    definition.versions.filter(is_current=True).update(is_current=False)
    next_number = (
        definition.versions.aggregate(highest=Max("version_number"))["highest"] or 0
    ) + 1
    version = ServiceVersion.objects.create(
        definition=definition,
        version_number=next_number,
        scope_summary=scope_summary.strip(),
        deliverables_summary=deliverables_summary.strip(),
        turnaround_days=turnaround_days,
        revision_allowance=revision_allowance,
        requires_quote=requires_quote,
        state=state,
        is_current=True,
    )
    audit_service.record(
        action="catalogue.version_published",
        actor=actor,
        resource_type="service_version",
        resource_id=str(version.pk),
        metadata={"service": definition.slug, "version": next_number, "state": state},
    )
    return version


def set_state(version: ServiceVersion, state: str, *, actor) -> None:
    if state not in ServiceState.values:
        raise ValidationFailure("Unknown service state.")
    if not version.is_current:
        raise ValidationFailure("Only the current version can change availability.")
    previous = version.state
    version.state = state
    version.save(update_fields=["state"])
    audit_service.record(
        action="catalogue.state_changed",
        actor=actor,
        resource_type="service_version",
        resource_id=str(version.pk),
        metadata={"from": previous, "to": state},
    )
