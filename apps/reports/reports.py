"""Reports for the Dedicated plan (pricing page: "Advanced reporting & insights").

Trends over the last 3, 6 or 12 finished months: requests sent and delivered, average
turnaround, work by service, money paid, and support replies on target. Each month
uses the same counting as the monthly summary (apps/reports/summary.py). Charts are
drawn here as plain SVG geometry, because the site's security policy allows no inline
scripts; the template only places the shapes.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from django.db.models import Sum
from django.utils import timezone

from . import summary

PERIODS = (3, 6, 12)
DEFAULT_PERIOD = 6
REPORTS_RANK = 3  # Dedicated

# Chart canvas (viewBox units); the SVG scales to its container.
W, H, LEFT, RIGHT, TOP = 520, 170, 34, 8, 10


@dataclass
class Report:
    months: list[date]
    rows: list[dict] = field(default_factory=list)
    by_service: list = field(default_factory=list)
    currencies: list[str] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    findings: dict = field(default_factory=dict)
    charts: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not any(r["sent"] or r["delivered"] for r in self.rows)


def report_months(organization, count: int, now=None) -> list[date]:
    """The last `count` finished months, not before the month the business signed up."""
    first, current = summary.available_months(organization, now)
    months, month = [], summary.previous_month(current)
    while len(months) < count and month >= first:
        months.append(month)
        month = summary.previous_month(month)
    return list(reversed(months))


def build(organization, count: int = DEFAULT_PERIOD, now=None) -> Report:
    from apps.billing.models import Invoice, InvoiceStatus

    now = now or timezone.now()
    report = Report(months=report_months(organization, count, now))
    services: Counter = Counter()
    for month in report.months:
        s = summary.build(organization, month, now=now)
        start, end = summary.month_bounds(month)
        paid = dict(Invoice.objects.filter(organization=organization, status=InvoiceStatus.PAID,
                                           paid_at__gte=start, paid_at__lt=end)
                    .values_list("currency").annotate(total=Sum("amount_minor")).order_by("currency"))
        services.update(dict(s.by_service))
        report.rows.append({
            "month": month, "sent": s.sent, "delivered": s.delivered, "turnaround": s.turnaround_days,
            "support_on_time": s.support_on_time, "support_counted": s.support_counted,
            "support_percent": s.support_percent, "paid": paid, "by_service": dict(s.by_service),
        })
    report.by_service = services.most_common()
    report.currencies = sorted({c for r in report.rows for c in r["paid"]})
    for r in report.rows:  # one cell per currency, in column order, for the template
        r["paid_cells"] = [(c, r["paid"].get(c, 0)) for c in report.currencies]
    _totals(report)
    _findings(report)
    _charts(report)
    return report


def _totals(report: Report) -> None:
    rows = report.rows
    turnarounds = [(r["turnaround"], r["delivered"]) for r in rows if r["turnaround"] is not None]
    weight = sum(d for _, d in turnarounds)
    report.totals = {
        "sent": sum(r["sent"] for r in rows),
        "delivered": sum(r["delivered"] for r in rows),
        "turnaround": round(sum(t * d for t, d in turnarounds) / weight, 1) if weight else None,
        "support_on_time": sum(r["support_on_time"] for r in rows),
        "support_counted": sum(r["support_counted"] for r in rows),
        "paid": {c: sum(r["paid"].get(c, 0) for r in rows) for c in report.currencies},
    }
    counted = report.totals["support_counted"]
    report.totals["support_percent"] = (round(report.totals["support_on_time"] / counted * 100)
                                        if counted else None)
    report.totals["support_late"] = counted - report.totals["support_on_time"]


def _findings(report: Report) -> None:
    """One plain sentence per chart, stating what it shows."""
    f = {}
    # Trends start from the first month with any requests, not an empty sign-up month.
    rows = report.rows
    while rows and not (rows[0]["sent"] or rows[0]["delivered"]):
        rows = rows[1:]
    if len(rows) >= 2:
        first, last = rows[0]["delivered"], rows[-1]["delivered"]
        trend = "rose" if last > first else "fell" if last < first else "stayed"
        f["delivered"] = (f"Delivered {trend} from {first} to {last} a month." if trend != "stayed"
                          else f"Delivered held at {last} a month.")
    elif rows:
        f["delivered"] = f"{rows[0]['delivered']} delivered in {rows[0]['month']:%B}."
    if rows:
        points = [r["turnaround"] for r in rows if r["turnaround"] is not None]
        if len(points) >= 2:
            word = "down" if points[-1] < points[0] else "up" if points[-1] > points[0] else "level"
            f["turnaround"] = (f"Business days from sending to delivery: {word} from {points[0]} to {points[-1]}."
                               if word != "level" else f"Business days from sending to delivery: steady at {points[-1]}.")
        elif points:
            f["turnaround"] = f"Business days from sending to delivery: {points[0]}."
    if report.by_service and report.totals.get("delivered"):
        name, count = report.by_service[0]
        share = round(count / report.totals["delivered"] * 100)
        f["services"] = f"{name} was {share}% of the {report.totals['delivered']} tasks delivered."
    report.findings = f


def _y(value, top) -> float:
    return TOP + (H - TOP) * (1 - value / top) if top else H


def _bars(series: list[list[float]], labels: list[str]) -> dict:
    """Grouped bars: one group per month, one bar per series."""
    top = max([v for s in series for v in s] + [1]) * 1.15
    n, g = len(labels), len(series)
    step = (W - LEFT - RIGHT) / max(n, 1)
    inner = max(step - 12, 4)
    bar_w = max(inner / g - 3, 2)
    groups = []
    for i, label in enumerate(labels):
        bars = []
        for j, s in enumerate(series):
            v = s[i]
            y = _y(v, top)
            bars.append({"series": j, "value": v, "x": round(LEFT + i * step + 6 + j * (inner / g), 1),
                         "y": round(y, 1), "w": round(bar_w, 1), "h": round(H - y, 1)})
        groups.append({"label": label, "x": round(LEFT + i * step + step / 2, 1), "bars": bars})
    grid = [round(TOP + (H - TOP) * k / 3, 1) for k in range(4)]
    return {"w": W, "h": H + 26, "base": H, "label_y": H + 16, "groups": groups, "grid": grid}


def _line(values: list[float | None], labels: list[str]) -> dict | None:
    known = [v for v in values if v is not None]
    if not known:
        return None
    lo, hi = min(known), max(known)
    lo, hi = max(0, int(lo) - 1), int(hi) + 1
    n = len(labels)
    pad = 22  # keep the first and last points clear of the axis labels
    step = (W - LEFT - RIGHT - 2 * pad) / max(n - 1, 1)
    x0 = LEFT + pad

    def y(v):
        return round(TOP + (H - TOP) * (1 - (v - lo) / (hi - lo)), 1)

    points = [{"x": round(x0 + i * step, 1), "y": y(v), "value": v, "label": labels[i]}
              for i, v in enumerate(values) if v is not None]
    ticks = [{"value": t, "y": y(t)} for t in range(lo, hi + 1)]
    return {"w": W, "h": H + 26, "label_y": H + 16, "points": points,
            "polyline": " ".join(f"{p['x']},{p['y']}" for p in points), "ticks": ticks,
            "labels": [{"x": round(x0 + i * step, 1), "label": lab} for i, lab in enumerate(labels)],
            "last": points[-1], "left": LEFT}


def _charts(report: Report) -> None:
    labels = [m.strftime("%b") for m in report.months]
    rows = report.rows
    report.charts["requests"] = _bars([[r["sent"] for r in rows], [r["delivered"] for r in rows]], labels)
    report.charts["turnaround"] = _line([r["turnaround"] for r in rows], labels)
    if report.currencies:
        main = max(report.currencies, key=lambda c: report.totals["paid"][c])
        report.charts["paid_currency"] = main
        report.charts["paid"] = _bars([[r["paid"].get(main, 0) / 100 for r in rows]], labels)
    top = report.by_service[0][1] if report.by_service else 1
    report.charts["services"] = [{"name": n, "count": c, "pct": round(c / top * 100)} for n, c in report.by_service]


# ---------- CSV ----------

def _safe(value) -> str:
    """Spreadsheet-safe text: a cell starting with = + - @ could run as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def csv_rows(report: Report):
    services = [name for name, _ in report.by_service]
    yield (["Month", "Requests sent", "Delivered", "Average turnaround (business days)",
            "Support first replies on time", "Support requests counted"]
           + [f"Paid ({c})" for c in report.currencies] + [f"Delivered: {s}" for s in services])
    for r in report.rows:
        yield ([r["month"].strftime("%Y-%m"), r["sent"], r["delivered"],
                "" if r["turnaround"] is None else r["turnaround"], r["support_on_time"], r["support_counted"]]
               + [f"{r['paid'].get(c, 0) / 100:.2f}" for c in report.currencies]
               + [r["by_service"].get(s, 0) for s in services])
    t = report.totals
    yield (["Total", t["sent"], t["delivered"], "" if t["turnaround"] is None else t["turnaround"],
            t["support_on_time"], t["support_counted"]]
           + [f"{t['paid'][c] / 100:.2f}" for c in report.currencies]
           + [dict(report.by_service)[s] for s in services])


def period_label(report: Report) -> str:
    if not report.months:
        return ""
    first, last = report.months[0], report.months[-1]
    if first == last:
        return first.strftime("%B %Y")
    return f"{first:%B %Y} to {last:%B %Y}" if first.year != last.year else f"{first:%B} to {last:%B %Y}"

