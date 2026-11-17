"""Plan & Usage, Billing & Invoices, Service Catalogue, Notifications and Team in the banda design."""
from __future__ import annotations

from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Invoice, InvoiceStatus, Subscription
from apps.communications.models import Notification, NotificationKind
from apps.organizations.models import Invitation

from .conftest import login_client


def _invoice(organization, number, amount, status=InvoiceStatus.ISSUED, **extra):
    return Invoice.objects.create(number=number, organization=organization, amount_minor=amount, currency="NGN",
                                  status=status, **extra)


# ---------- Billing & Invoices ----------

def test_billing_page_totals_what_is_owed_and_paid(client, organization):
    _invoice(organization, "INV-B1", 150000)
    _invoice(organization, "INV-B2", 50000)
    _invoice(organization, "INV-B3", 70000, status=InvoiceStatus.PAID, paid_at=timezone.now())

    page = client.get(reverse("billing-home"))
    assert page.templates[0].name == "banda/app/billing.html"
    assert page.context["owed"] == {"NGN": 200000} and page.context["owed_count"] == 2
    assert page.context["paid"] == {"NGN": 70000}
    body = page.content.decode()
    assert "NGN 2,000.00" in body and "INV-B3" in body


def test_billing_filters_and_search(client, organization):
    _invoice(organization, "INV-OPEN-1", 1000)
    _invoice(organization, "INV-PAID-1", 1000, status=InvoiceStatus.PAID, paid_at=timezone.now())

    open_only = client.get(reverse("billing-home"), {"status": "open"}).content.decode()
    assert "INV-OPEN-1" in open_only and "INV-PAID-1" not in open_only
    search = client.get(reverse("billing-home"), {"q": "paid-1"}).content.decode()
    assert "INV-PAID-1" in search and "INV-OPEN-1" not in search


def test_overdue_invoices_are_marked(client, organization):
    _invoice(organization, "INV-LATE", 1000, due_at=timezone.now() - timedelta(days=2))
    assert "Overdue" in client.get(reverse("billing-home")).content.decode()


@override_settings(PAYMENTS_ENABLED=True)
def test_only_the_owner_sees_a_pay_button(client, organization, member):
    invoice = _invoice(organization, "INV-PAY", 1000)
    pay_url = reverse("invoice-checkout", args=[invoice.pk])
    assert pay_url in client.get(reverse("billing-home")).content.decode()
    member_view = login_client(member).get(reverse("billing-home")).content.decode()
    assert pay_url not in member_view and "Only the business owner can pay invoices." in member_view


@override_settings(PAYMENTS_ENABLED=False)
def test_no_pay_button_while_payments_are_closed(client, organization):
    invoice = _invoice(organization, "INV-CLOSED", 1000)
    body = client.get(reverse("billing-home")).content.decode()
    assert reverse("invoice-checkout", args=[invoice.pk]) not in body
    assert "Card payment isn't open yet." in body


def test_billing_never_shows_another_business(client, other_organization):
    _invoice(other_organization, "INV-OTHER", 1000)
    assert "INV-OTHER" not in client.get(reverse("billing-home")).content.decode()


# ---------- Plan & Usage ----------

def test_plan_page_without_a_plan_offers_one(client):
    page = client.get(reverse("plan-home"))
    assert page.templates[0].name == "banda/app/plan.html"
    assert "Without a plan" in page.content.decode()


def test_plan_page_shows_usage_and_renewal(client, subscription):
    body = client.get(reverse("plan-home")).content.decode()
    assert "Tasks used" in body and "Tasks left" in body
    assert "Cancel renewal" in body and reverse("plan-cancel") in body


def test_a_cancelled_renewal_can_be_kept(client, subscription):
    Subscription.objects.filter(pk=subscription.pk).update(cancel_at_period_end=True)
    body = client.get(reverse("plan-home")).content.decode()
    assert "Keep my plan" in body and reverse("plan-resume") in body


def test_a_member_cannot_change_the_renewal(organization, member, subscription):
    body = login_client(member).get(reverse("plan-home")).content.decode()
    assert reverse("plan-cancel") not in body
    assert "Only the business owner can change the renewal." in body


# ---------- Service Catalogue ----------

def test_catalogue_searches_and_filters(client, service_version):
    page = client.get(reverse("service-catalogue"))
    assert page.templates[0].name == "banda/app/catalogue.html"
    category = service_version.definition.category
    from django.utils.html import escape

    assert escape(category.name) in page.content.decode()  # the start page lists categories
    name = service_version.definition.name
    found = client.get(reverse("service-catalogue"), {"q": name})
    assert name in found.content.decode()
    assert f"?service={service_version.pk}" in found.content.decode()

    miss = client.get(reverse("service-catalogue"), {"q": "zzz-no-such-service"})
    assert miss.context["versions"] == [] and "No task matches that" in miss.content.decode()

    slug = service_version.definition.category.slug
    hit = client.get(reverse("service-catalogue"), {"category": slug})
    assert [v.pk for v in hit.context["versions"]] == [service_version.pk]


# ---------- Notifications ----------

def test_notifications_filter_unread_and_staff_get_their_own_page(client, owner, organization, ops_user):
    Notification.objects.create(user=owner, organization=organization, kind=NotificationKind.BILLING,
                                title="Synthetic unread note")
    Notification.objects.create(user=owner, organization=organization, kind=NotificationKind.SUPPORT,
                                title="Synthetic read note", read_at=timezone.now())
    page = client.get(reverse("notifications"))
    assert page.templates[0].name == "banda/app/notifications.html"
    body = page.content.decode()
    assert "Synthetic unread note" in body and "Synthetic read note" in body and "Mark all as read" in body
    unread = client.get(reverse("notifications"), {"show": "unread"}).content.decode()
    assert "Synthetic unread note" in unread and "Synthetic read note" not in unread

    staff_page = login_client(ops_user).get(reverse("notifications"))
    assert staff_page.templates[0].name == "banda/staff/notifications.html"


# ---------- Organization & Team ----------

def test_team_page_lets_only_the_owner_invite(client, organization, member):
    page = client.get(reverse("team-home"))
    assert page.templates[0].name == "banda/app/team.html"
    body = page.content.decode()
    assert reverse("team-invite") in body and "member@example.test" in body

    member_view = login_client(member).get(reverse("team-home")).content.decode()
    assert reverse("team-invite") not in member_view
    assert "Only the business owner can invite or remove members." in member_view


def test_invitation_page_uses_the_banda_sign_in_layout(client, member):
    page = login_client(member).get("/invite/not-a-real-token/")
    assert page.templates[0].name == "banda/auth/invitation.html"
    assert "This invitation is no longer valid" in page.content.decode()
    assert not Invitation.objects.exists()


def test_the_owner_can_remove_a_member(client, organization, member):
    from apps.organizations.models import Membership

    membership = Membership.objects.get(organization=organization, user=member)
    url = reverse("team-member-remove", args=[membership.pk])
    assert url in client.get(reverse("team-home")).content.decode()
    assert client.post(url).status_code == 302
    assert not Membership.objects.filter(pk=membership.pk).exists()


def test_a_member_cannot_remove_anyone(organization, owner, member):
    from apps.organizations.models import Membership

    owner_membership = Membership.objects.get(organization=organization, user=owner)
    login_client(member).post(reverse("team-member-remove", args=[owner_membership.pk]))
    assert Membership.objects.filter(pk=owner_membership.pk).exists()
