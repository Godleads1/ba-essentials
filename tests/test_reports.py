"""Reports page and CSV for the Dedicated plan (pricing page: "Advanced reporting & insights")."""
from __future__ import annotations

import csv
import io
from datetime import date

import pytest
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.billing.models import Invoice, InvoiceStatus, Subscription
from apps.reports import reports

from . import test_monthly_summary
from .test_monthly_summary import NOW, _plan, _task, at

pytestmark = pytest.mark.django_db
october = test_monthly_summary.october  # the same October data as the summary tests


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: NOW)


def _paid(organization, number, minor, when, currency="NGN"):
    return Invoice.objects.create(number=number, organization=organization, amount_minor=minor, currency=currency,
                                  status=InvoiceStatus.PAID, paid_at=when)


def test_months_are_finished_and_not_before_signup(october):
    assert reports.report_months(october, 6, now=NOW) == [date(2026, 9, 1), date(2026, 10, 1)]
    assert reports.report_months(october, 3, now=NOW)[-1] == date(2026, 10, 1)   # November isn't finished


def test_figures_totals_and_findings(october):
    _plan(october, "dedicated", 3, 30)
    _paid(october, "INV-1", 15_000_000, at(3))
    _paid(october, "INV-2", 2_500, at(4), currency="USD")
    r = reports.build(october, 6, now=NOW)
    oct_row = r.rows[-1]
    assert (oct_row["sent"], oct_row["delivered"]) == (3, 3)
    assert oct_row["paid"] == {"NGN": 15_000_000, "USD": 2_500}
    assert r.currencies == ["NGN", "USD"]
    assert r.totals["support_counted"] == 2 and r.totals["support_late"] == 1 and r.totals["support_percent"] == 50
    assert r.charts["paid_currency"] == "NGN"
    assert "rose" in r.findings["delivered"]          # September 0 -> October 3
    assert r.findings["services"].endswith("of the 3 tasks delivered.")
    assert not r.empty


def test_csv_guards_against_formulas():
    assert reports._safe("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")"
    assert reports._safe("+1") == "'+1" and reports._safe("@SUM") == "'@SUM" and reports._safe("-2") == "'-2"
    assert reports._safe("Legal review") == "Legal review" and reports._safe(None) == ""


def test_growth_and_starter_see_what_it_offers(client, october):
    page = client.get("/app/reports/").content.decode()
    assert "Reports come with Dedicated" in page
    _plan(october)                                    # Growth
    page = client.get("/app/reports/").content.decode()
    assert "Reports come with Dedicated" in page and "Supplier contract review" not in page
    assert client.get("/app/reports/export.csv").status_code == 404


def test_dedicated_sees_the_charts(client, october):
    _plan(october, "dedicated", 3, 30)
    _paid(october, "INV-1", 15_000_000, at(3))
    page = client.get("/app/reports/").content.decode()
    body = page.split(">Reports</h1>")[1].split('id="rpSup"')[0]
    assert "Requests sent and delivered" in body and "<svg" in body and "<script" not in body
    assert "September to October 2026" in page
    assert "NGN 150,000.00" in page
    assert "Download CSV" in page and "/app/reports/export.csv?months=6" in page


def test_bad_periods_fall_back(client, october):
    _plan(october, "dedicated", 3, 30)
    for raw in ("nonsense", "5", "-3", "120"):
        page = client.get(f"/app/reports/?months={raw}").content.decode()
        assert '<option value="6" selected>' in page


def test_csv_download(client, owner, october):
    _plan(october, "dedicated", 3, 30)
    _paid(october, "INV-1", 15_000_000, at(3))
    response = client.get("/app/reports/export.csv?months=3")
    assert response.status_code == 200
    assert response["Content-Disposition"] == 'attachment; filename="reports-2026-09_2026-10.csv"'
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    assert rows[0][:3] == ["Month", "Requests sent", "Delivered"] and "Paid (NGN)" in rows[0]
    assert [row[0] for row in rows[1:]] == ["2026-09", "2026-10", "Total"]
    assert rows[2][rows[0].index("Paid (NGN)")] == "150000.00"
    event = AuditEvent.objects.get(action="reports.exported")
    assert event.actor == owner and event.metadata == {"months": 3}


def test_a_task_title_cannot_become_a_formula(client, make_task, owner, october):
    """Service names come from our catalogue, but nothing typed by a client reaches the CSV either."""
    _plan(october, "dedicated", 3, 30)
    _task(make_task, october, owner, "=cmd|' /C calc'!A0", at(14), at(15))
    text = client.get("/app/reports/export.csv").content.decode()
    assert "=cmd" not in text


def test_every_csv_cell_is_defused(client, october, monkeypatch):
    """Whatever a row holds, a cell starting with = + - @ reaches the spreadsheet as text."""
    _plan(october, "dedicated", 3, 30)
    monkeypatch.setattr(reports, "csv_rows", lambda report: iter([["=SUM(A1)", "+1", "@x", 3]]))
    text = client.get("/app/reports/export.csv").content.decode().lstrip("\ufeff")
    assert next(csv.reader(io.StringIO(text))) == ["'=SUM(A1)", "'+1", "'@x", "3"]


def test_another_business_sees_only_its_own(other_client, other_organization, october):
    _plan(october, "dedicated", 3, 30)
    _paid(october, "INV-1", 15_000_000, at(3))
    _plan(other_organization, "dedicated-b", 3, 30)
    page = other_client.get("/app/reports/").content.decode()
    assert "NGN 150,000.00" not in page and "Your reports fill in as you use banda" in page
    assert "150000.00" not in other_client.get("/app/reports/export.csv").content.decode()


def test_signed_out_is_sent_to_sign_in(october):
    from django.test import Client

    response = Client().get("/app/reports/")
    assert response.status_code == 302 and response["Location"].startswith("/login")


def test_summary_links_to_reports_only_for_dedicated(client, october):
    _plan(october)
    assert "Open Reports" not in client.get("/app/summary/?month=2026-10").content.decode()
    Subscription.objects.filter(organization=october).delete()
    _plan(october, "dedicated", 3, 30)
    assert "Open Reports" in client.get("/app/summary/?month=2026-10").content.decode()
