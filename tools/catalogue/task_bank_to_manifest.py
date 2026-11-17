"""Turn the founder's task bank spreadsheet into the JSON manifest bootstrap_catalogue loads.

Usage (development only; needs openpyxl, which the app itself does not):
    pip install openpyxl
    python tools/catalogue/task_bank_to_manifest.py Banda_Service_Catalogue_DEVELOPER_v2.1.xlsx \\
        > catalogue/service_catalogue_v2.1.json
    python manage.py bootstrap_catalogue catalogue/service_catalogue_v2.1.json

The staff notes (the sheet's Note column) are left out by default, because this repository
is public. To load them too, write a private copy with --with-notes, load it, and don't
commit it:
    python tools/catalogue/task_bank_to_manifest.py the.xlsx --with-notes > /tmp/private.json

Launch statuses follow the sheet: Launch is available, Scoping only and Quoted / referral
are quoted first, Gated is not offered. Each task can be switched on or off later in the
staff catalogue.
"""
from __future__ import annotations

import json
import sys

import openpyxl

CATEGORIES = {
    # family: (slug, sort order, minimum plan rank, uses the legal allowance, summary)
    "Business & Admin": ("business-admin", 10, 0, False,
                         ("Proposal and document support, invoicing and payment follow-up, scheduling, outreach, "
                         "filing and data entry.")),
    "Legal & IP Review": ("legal-ip-review", 20, 0, True,
                          ("Contract review and drafting, CAC business registration, trademarks, and HR and privacy "
                          "policies. Uses your Legal & IP allowance.")),
    "Operations & Logistics": ("operations", 30, 2, False,
                               ("Vendor and freelancer coordination, travel and event logistics, project tracking, "
                               "and weekly or monthly reports.")),
    "Tax Readiness": ("tax-readiness", 40, 2, False,
                      ("Income summaries and expense categories organised into an export package for your own "
                      "tax expert.")),
    "Specialized Add-ons": ("specialized-add-ons", 50, 3, False,
                            ("Social media management, bookkeeping, copywriting, CRM setup and translation, priced "
                            "separately from your plan.")),
    "Other / Not Sure": ("other", 90, 0, False,
                         "Not sure where your task fits? Send it anyway and a coordinator will scope it with you."),
}
STATES = {"Launch": "available", "Scoping only": "scoping_only", "Quoted / referral": "scoping_only",
          "Gated": "unavailable"}
LANES = {"General": "general", "Legal": "legal", "Quoted": "quoted", "Workspace": "workspace", "Scoping": "scoping"}
TAGS = ("GEN", "FIN", "VAS", "PRO", "OGE", "CRE", "HLT", "ECO")


def text(value) -> str:
    return " ".join(str(value or "").split())


def main(path: str, with_notes: bool = False) -> None:
    sheet = openpyxl.load_workbook(path, data_only=True)["Task bank"]
    rows = list(sheet.iter_rows(values_only=True))
    header = rows[0]
    categories: dict[str, dict] = {}
    for raw in rows[1:]:
        if not raw[0]:
            continue
        row = dict(zip(header, raw))
        family = row["Family"]
        slug, order, rank, legal, summary = CATEGORIES[family]
        category = categories.setdefault(family, {
            "name": family, "slug": slug, "summary": summary, "sort_order": order,
            "minimum_plan_rank": rank, "uses_legal_allowance": legal, "services": [],
        })
        status = row["Proposed launch status"]
        lane = LANES[row["Allowance lane"]]
        weight = row["Weight"] if isinstance(row["Weight"], int) else 0
        gets = text(row["What the client gets"])
        category["services"].append({
            "code": row["Code"],
            "slug": row["Code"].lower().replace(".", "-"),
            "name": text(row["Task"]),
            "subtype": text(row["Subtype"]),
            "role": text(row["Role (queue)"]),
            "scope_summary": gets,
            "deliverables_summary": gets,
            "client_provides": text(row["Client provides"]),
            "size_rule": text(row["Size band and weight rule"]),
            "allowance_lane": lane,
            "weight": weight,
            "state": STATES[status],
            "requires_quote": status != "Launch" or lane == "quoted",
            "industries": [t for t in TAGS if row.get(t) == "Y"],
            "creator": row.get("Creator Essentials") == "Y",
            **({"note": text(row.get("Note"))} if with_notes else {}),
        })
    manifest = {
        "_source": "Banda Service Catalogue / task bank v2.1 (16 November 2026), Task bank sheet.",
        "categories": sorted(categories.values(), key=lambda c: c["sort_order"]),
    }
    json.dump(manifest, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1], with_notes="--with-notes" in sys.argv[2:])
