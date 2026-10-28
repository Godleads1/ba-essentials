from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.catalogue import services
from apps.catalogue.models import (
    ServiceCategory,
    ServiceDefinition,
    ServiceState,
    ServiceVersion,
)


class Command(BaseCommand):
    help = (
        "Load service categories and services from a JSON manifest. Publishing a "
        "different scope creates a new immutable service version."
    )

    def add_arguments(self, parser):
        parser.add_argument("manifest", help="Path to the JSON manifest")
        parser.add_argument(
            "--dry-run", action="store_true", help="Validate and report without writing"
        )

    def handle(self, *args, **options):
        path = Path(options["manifest"])
        if not path.is_file():
            raise CommandError(f"Manifest not found: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandError(f"Manifest is not valid JSON: {exc}") from exc

        categories = payload.get("categories")
        if not isinstance(categories, list) or not categories:
            raise CommandError("Manifest must contain a non-empty 'categories' list.")

        dry_run = options["dry_run"]
        changed = 0
        for category_data in categories:
            changed += self._category(category_data, dry_run)

        suffix = " (dry run, nothing written)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(f"Catalogue up to date; {changed} change(s){suffix}.")
        )

    def _category(self, data: dict, dry_run: bool) -> int:
        name = (data.get("name") or "").strip()
        if not name:
            raise CommandError("Every category needs a name.")
        slug = (data.get("slug") or slugify(name)).strip()
        services_data = data.get("services") or []
        if not isinstance(services_data, list):
            raise CommandError(f"'services' for {name} must be a list.")

        if dry_run:
            self.stdout.write(f"Would ensure category {name} ({slug}).")
            return 1

        category, _ = ServiceCategory.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "summary": (data.get("summary") or "").strip()[:255],
                "sort_order": int(data.get("sort_order") or 0),
                "is_active": bool(data.get("is_active", True)),
            },
        )
        changed = 1
        for service_data in services_data:
            changed += self._service(category, service_data)
        return changed

    def _service(self, category: ServiceCategory, data: dict) -> int:
        name = (data.get("name") or "").strip()
        scope = (data.get("scope_summary") or "").strip()
        deliverables = (data.get("deliverables_summary") or "").strip()
        if not name or not scope or not deliverables:
            raise CommandError(
                "Every service needs name, scope_summary and deliverables_summary."
            )

        state = data.get("state") or ServiceState.SCOPING_ONLY
        if state not in ServiceState.values:
            raise CommandError(f"Unknown service state {state!r} for {name}.")

        slug = (data.get("slug") or slugify(name)).strip()
        definition, _ = ServiceDefinition.objects.update_or_create(
            slug=slug,
            defaults={
                "category": category,
                "name": name,
                "is_active": bool(data.get("is_active", True)),
            },
        )
        settings_payload = {
            "scope_summary": scope,
            "deliverables_summary": deliverables,
            "turnaround_days": int(data.get("turnaround_days") or 7),
            "revision_allowance": int(data.get("revision_allowance") or 1),
            "requires_quote": bool(data.get("requires_quote", True)),
            "state": state,
        }
        current = definition.versions.filter(is_current=True).first()
        if current is not None and all(
            getattr(current, key) == value for key, value in settings_payload.items()
        ):
            return 0

        with transaction.atomic():
            version: ServiceVersion = services.publish_version(
                definition,
                scope_summary=scope,
                deliverables_summary=deliverables,
                turnaround_days=settings_payload["turnaround_days"],
                revision_allowance=settings_payload["revision_allowance"],
                requires_quote=settings_payload["requires_quote"],
                state=state,
                actor=None,
            )
        self.stdout.write(
            f"  {name}: published v{version.version_number} ({state})."
        )
        return 1
