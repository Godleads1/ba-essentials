"""Support reply targets by plan and queue priority for Growth and Dedicated (Terms 3.6)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.billing.models import Plan, Subscription, SubscriptionStatus
from apps.communications import services as communications
from apps.communications.models import SupportTicket
from apps.communications.service_levels import target_hours, target_label
from apps.core.business_time import add_business_hours

LAGOS = ZoneInfo("Africa/Lagos")
pytestmark = pytest.mark.django_db


def at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=LAGOS)


# ---------- business hours ----------

def test_inside_hours_adds_directly():
    assert add_business_hours(at(2026, 11, 26, 10), 4) == at(2026, 11, 26, 14)  # Thursday


def test_runs_over_into_the_next_business_day():
    assert add_business_hours(at(2026, 11, 26, 15), 4) == at(2026, 11, 27, 11)


def test_skips_the_weekend():
    assert add_business_hours(at(2026, 11, 27, 16), 4) == at(2026, 11, 30, 12)  # Friday 4pm -> Monday noon


def test_out_of_hours_starts_at_next_opening():
    assert add_business_hours(at(2026, 11, 28, 22), 1) == at(2026, 11, 30, 10)  # Saturday night
    assert add_business_hours(at(2026, 11, 26, 7), 1) == at(2026, 11, 26, 10)   # before opening


def test_skips_listed_public_holidays(settings):
    settings.SUPPORT_HOLIDAYS = ["2026-12-25", "2026-12-28"]
    # Thursday 24 Dec 4pm + 4h: 1h left Thursday, Fri 25 holiday, weekend, Mon 28 holiday -> Tue 29 12:00
    assert add_business_hours(at(2026, 12, 24, 16), 4) == at(2026, 12, 29, 12)


def test_bad_holiday_entries_are_ignored(settings):
    settings.SUPPORT_HOLIDAYS = ["not-a-date", ""]
    assert add_business_hours(at(2026, 11, 26, 10), 1) == at(2026, 11, 26, 11)


def test_targets_by_plan():
    assert [target_hours(r) for r in (0, 1, 2, 3)] == [16, 16, 8, 4]
    assert target_label(3) == "4 business hours" and target_label(2) == "1 business day"
    assert target_label(1) == "2 business days"


# ---------- tickets ----------

def _plan(organization, slug, rank):
    plan = Plan.objects.create(name=slug.title(), slug=slug, rank=rank, monthly_task_allowance=5, price_minor=0)
    now = timezone.now()
    return Subscription.objects.create(organization=organization, plan=plan, status=SubscriptionStatus.ACTIVE,
                                       period_start=now - timedelta(days=1), period_end=now + timedelta(days=29))


def test_ticket_records_plan_and_due_time(organization, owner):
    _plan(organization, "dedicated", 3)
    ticket = communications.create_ticket(organization=organization, author=owner,
                                          subject="Need help with an invoice", body="Hi")
    assert ticket.plan_rank == 3 and ticket.plan_label == "Dedicated"
    assert ticket.reply_due_at == add_business_hours(ticket.last_message_at, 4)
    assert ticket.first_reply_at is None


def test_no_plan_gets_the_starter_target(organization, owner):
    ticket = communications.create_ticket(organization=organization, author=owner,
                                          subject="Question before buying", body="Hi")
    assert ticket.plan_rank == 0
    assert ticket.reply_due_at == add_business_hours(ticket.last_message_at, 16)


def test_first_public_staff_reply_stops_the_clock(organization, owner, ops_user):
    ticket = communications.create_ticket(organization=organization, author=owner,
                                          subject="Need help with an invoice", body="Hi")
    communications.post_ticket_message(ticket=ticket, author=ops_user, body="Internal note", internal=True)
    ticket.refresh_from_db()
    assert ticket.first_reply_at is None  # an internal note doesn't count
    communications.post_ticket_message(ticket=ticket, author=ops_user, body="On it")
    ticket.refresh_from_db()
    first = ticket.first_reply_at
    assert first is not None
    communications.post_ticket_message(ticket=ticket, author=owner, body="Thanks")
    communications.post_ticket_message(ticket=ticket, author=ops_user, body="Done")
    ticket.refresh_from_db()
    assert ticket.first_reply_at == first  # only the first reply is recorded


def test_overdue_only_while_waiting(organization, owner):
    ticket = communications.create_ticket(organization=organization, author=owner,
                                          subject="Need help with an invoice", body="Hi")
    SupportTicket.objects.filter(pk=ticket.pk).update(reply_due_at=timezone.now() - timedelta(hours=1))
    ticket.refresh_from_db()
    assert ticket.reply_overdue
    SupportTicket.objects.filter(pk=ticket.pk).update(first_reply_at=timezone.now())
    ticket.refresh_from_db()
    assert not ticket.reply_overdue


def test_support_queue_lists_waiting_soonest_due_first(staff_client, organization, other_organization, owner, other_owner):
    _plan(other_organization, "dedicated", 3)
    starter = communications.create_ticket(organization=organization, author=owner,
                                           subject="Starter question here", body="Hi")
    communications.create_ticket(organization=other_organization, author=other_owner,
                                 subject="Dedicated question here", body="Hi")
    SupportTicket.objects.filter(pk=starter.pk).update(reply_due_at=timezone.now() - timedelta(hours=2))
    page = staff_client.get("/staff/support/").content.decode()
    assert page.index("Starter question here") < page.index("Dedicated question here")  # overdue first
    assert "Overdue since" in page and "Dedicated" in page and "1 waiting past the first-reply target" in page


def test_client_sees_target_and_expected_time(client, organization, owner):
    _plan(organization, "growth", 2)
    assert "within 1 business day" in client.get("/app/support/new/").content.decode()
    ticket = communications.create_ticket(organization=organization, author=owner,
                                          subject="Need help with an invoice", body="Hi")
    assert "Reply expected by" in client.get(f"/app/support/{ticket.pk}/").content.decode()


# ---------- task queue priority ----------

def test_task_queue_lists_higher_plans_first(staff_client, make_task, organization, other_organization, owner, other_owner):
    make_task(organization, owner, title="Starter business request")
    _plan(other_organization, "dedicated", 3)
    make_task(other_organization, other_owner, title="Dedicated business request")
    page = staff_client.get("/staff/queue/").content.decode()
    assert page.index("Dedicated business request") < page.index("Starter business request")


# ---------- the promise matches the page ----------

def test_pricing_and_terms_say_what_is_delivered(db):
    from django.test import Client

    landing = Client().get("/").content.decode()
    assert "Faster turnaround" not in landing and "SLA options" not in landing
    assert "Priority in our work queue" in landing and "Written reply target: 4 business hours" in landing
    terms = Client().get("/terms/").content.decode()
    assert "Dedicated, 4 hours; Growth, 1 business day; Starter, or no plan, 2 business days" in terms
