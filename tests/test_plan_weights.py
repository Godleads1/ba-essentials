"""Catalogue step 2: requests use the plan by weight and lane (Service Catalogue v2.1, Points rules)."""
from __future__ import annotations

from django.urls import reverse

from apps.billing import plan_rules
from apps.billing import services as billing
from apps.billing.models import Invoice, UsageKind, UsageLedger
from apps.catalogue.models import ServiceVersion
from apps.quotes import services as quotes


def _set(task, **fields):
    ServiceVersion.objects.filter(pk=task.service_version_id).update(**fields)
    task.service_version.refresh_from_db()
    return task


def _quote_and_accept(task, approver, owner, *, plan_units=None, key="w"):
    version = quotes.create_version(task, approver, scope_summary="Synthetic scope for a weighted request.",
                                    exclusions="", amount_minor=150_000, turnaround_days=5, revision_allowance=1,
                                    plan_units=plan_units)
    version = quotes.send_version(task, version, approver)
    return quotes.accept_quote(task=task, version=version, actor=owner, idempotency_key=key)


def _units(task, kind=UsageKind.ALLOWANCE):
    return sum(UsageLedger.objects.filter(task=task, kind=kind).values_list("units", flat=True))


def test_a_weight_two_task_uses_two_tasks(scoping_task, quote_approver, owner, subscription, organization):
    _set(scoping_task, weight=2)
    outcome = _quote_and_accept(scoping_task, quote_approver, owner)
    assert outcome.covered_by_allowance is True
    assert _units(scoping_task) == 2
    assert billing.allowance_remaining(organization) == 0


def test_not_enough_left_means_the_quote_is_paid_instead(scoping_task, quote_approver, owner, subscription,
                                                         organization):
    _set(scoping_task, weight=3)  # the Starter fixture plan has 2 tasks
    outcome = _quote_and_accept(scoping_task, quote_approver, owner)
    assert outcome.covered_by_allowance is False
    assert Invoice.objects.filter(task=scoping_task).exists()
    assert billing.allowance_remaining(organization) == 2


def test_staff_can_size_a_bigger_request_on_the_quote(scoping_task, quote_approver, owner, subscription,
                                                      organization):
    outcome = _quote_and_accept(scoping_task, quote_approver, owner, plan_units=2)
    assert outcome.covered_by_allowance is True and _units(scoping_task) == 2


def test_legal_lane_uses_legal_reviews_by_weight(scoping_task, quote_approver, owner, subscription, organization):
    subscription.plan.legal_review_allowance = 4
    subscription.plan.save(update_fields=["legal_review_allowance"])
    _set(scoping_task, allowance_lane="legal", weight=2)
    outcome = _quote_and_accept(scoping_task, quote_approver, owner)
    assert outcome.covered_by_allowance is True
    assert _units(scoping_task, UsageKind.LEGAL) == 2 and _units(scoping_task) == 0
    assert billing.allowance_remaining(organization) == 2


def test_quoted_services_never_use_the_plan(scoping_task, quote_approver, owner, subscription, organization):
    _set(scoping_task, allowance_lane="quoted", weight=0)
    outcome = _quote_and_accept(scoping_task, quote_approver, owner)
    assert outcome.covered_by_allowance is False
    assert not UsageLedger.objects.filter(task=scoping_task).exists()


def test_a_legal_task_marked_quoted_in_the_sheet_is_priced_separately(scoping_task, quote_approver, owner,
                                                                       subscription):
    _set(scoping_task, allowance_lane="legal", weight=0)
    assert plan_rules.lane_for(scoping_task.service_version) == plan_rules.LANE_QUOTED
    assert _quote_and_accept(scoping_task, quote_approver, owner).covered_by_allowance is False


def test_workspace_is_covered_without_using_tasks(scoping_task, quote_approver, owner, subscription, organization):
    _set(scoping_task, allowance_lane="workspace", weight=0)
    outcome = _quote_and_accept(scoping_task, quote_approver, owner)
    assert outcome.covered_by_allowance is True
    assert billing.allowance_remaining(organization) == 2


def test_cancelling_gives_back_every_task_it_used(scoping_task, quote_approver, owner, subscription, organization):
    from apps.tasks import services as task_services

    _set(scoping_task, weight=2)
    _quote_and_accept(scoping_task, quote_approver, owner)
    scoping_task.refresh_from_db()
    task_services.cancel_by_client(scoping_task, owner, "Not needed after all")
    assert billing.allowance_remaining(organization) == 2


def test_older_quote_fingerprints_are_unchanged(scoping_task, quote_approver):
    import hashlib

    version = quotes.create_version(scoping_task, quote_approver, scope_summary="Synthetic scope, unchanged.",
                                    exclusions="", amount_minor=1000, turnaround_days=3, revision_allowance=1)
    old = hashlib.sha256("|".join([str(version.pk), str(version.version_number), version.scope_summary,
                                   version.exclusions, str(version.amount_minor), version.currency,
                                   str(version.turnaround_days), str(version.revision_allowance),
                                   version.payment_terms]).encode("utf-8")).hexdigest()
    assert quotes.terms_fingerprint(version) == old


# ---------- the preview before sending ----------

def test_preview_says_what_a_request_will_use(organization, subscription, service_version):
    text = plan_rules.preview(organization, service_version)["text"]
    assert text.startswith("This will use 1 task of your 2 (1 left this month")
    service_version.weight = 3
    over = plan_rules.preview(organization, service_version)
    assert over["fits"] is False and "we'll quote the extra" in over["text"]
    service_version.allowance_lane = "quoted"
    assert "uses none of your plan" in plan_rules.preview(organization, service_version)["text"]


def test_preview_without_a_plan(organization, service_version):
    assert "You don't have a plan" in plan_rules.preview(organization, service_version)["text"]


def test_submit_page_shows_the_preview_and_both_balances(client, subscription, service_version):
    page = client.get(reverse("task-submit"), {"service": str(service_version.pk)})
    body = page.content.decode()
    assert "This will use 1 task of your 2" in body
    assert 'id="planPreviews"' in body and "Left in your plan" in body and "Legal reviews" in body
    assert "/static/banda/submit-preview.js" in body
    assert "Not in your plan" in body  # the fixture plan has no legal reviews


def test_client_sees_the_plan_cover_a_sent_quote(scoping_task, quote_approver, client, subscription):
    version = quotes.create_version(scoping_task, quote_approver, scope_summary="Synthetic scope for the client.",
                                    exclusions="", amount_minor=150_000, turnaround_days=5, revision_allowance=1)
    quotes.send_version(scoping_task, version, quote_approver)
    body = client.get(reverse("task-detail", args=[scoping_task.pk])).content.decode()
    assert "Your plan covers this. Accepting uses 1 task of your 2" in body
