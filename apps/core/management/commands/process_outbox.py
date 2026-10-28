from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import OutboxEvent

logger = logging.getLogger("ba.outbox")

MAX_ATTEMPTS = 10


class Command(BaseCommand):
    help = "Deliver pending outbox events (email notifications) at least once."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        limit = max(1, min(options["limit"], 1000))
        events = list(
            OutboxEvent.objects.filter(processed_at__isnull=True, attempts__lt=MAX_ATTEMPTS)
            .order_by("created_at")[:limit]
        )
        if options["dry_run"]:
            self.stdout.write(f"{len(events)} event(s) pending.")
            return

        delivered = 0
        failed = 0
        for event in events:
            try:
                self._dispatch(event)
            except Exception as exc:  # keep the queue moving; record safely
                failed += 1
                event.attempts += 1
                event.last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
                event.save(update_fields=["attempts", "last_error"])
                logger.warning(
                    "outbox_failed event=%s topic=%s attempt=%s error=%s",
                    event.pk,
                    event.topic,
                    event.attempts,
                    type(exc).__name__,
                )
            else:
                delivered += 1
                event.attempts += 1
                event.processed_at = timezone.now()
                event.last_error = ""
                event.save(update_fields=["attempts", "processed_at", "last_error"])

        self.stdout.write(
            self.style.SUCCESS(f"Delivered {delivered}; failed {failed}.")
        )

    def _dispatch(self, event: OutboxEvent) -> None:
        if event.topic == OutboxEvent.Topic.EMAIL:
            payload = event.payload or {}
            recipients = payload.get("to") or []
            if isinstance(recipients, str):
                recipients = [recipients]
            if not recipients or not payload.get("subject") or not payload.get("body"):
                raise ValueError("Email event needs to, subject and body.")
            send_mail(
                str(payload["subject"])[:200],
                str(payload["body"]),
                payload.get("from") or settings.DEFAULT_FROM_EMAIL,
                [str(address) for address in recipients],
                fail_silently=False,
            )
            return
        if event.topic == OutboxEvent.Topic.NOTIFICATION:
            # In-app notifications are not implemented yet; the event is kept
            # until a handler exists rather than silently discarded.
            raise NotImplementedError("Notification delivery is not implemented.")
        raise ValueError(f"Unknown outbox topic {event.topic!r}")
