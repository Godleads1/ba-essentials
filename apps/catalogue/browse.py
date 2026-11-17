"""Browsing the Service Catalogue: shared by the client app and the public services page.

Only tasks that can be requested are listed (Gated tasks stay out), and only the fields a client
already sees. Staff notes are never in the database the site reads from.
"""
from __future__ import annotations

from django.db.models import Q

from apps.billing import plan_rules

from . import guides as guide_data
from . import services
from .models import INDUSTRIES


def public_access(category) -> plan_rules.CategoryAccess:
    """For visitors: every plan includes rank-0 categories; the rest name the smallest plan that has them."""
    minimum = getattr(category, "minimum_plan_rank", 0) or 0
    if minimum == 0:
        return plan_rules.CategoryAccess(True)
    return plan_rules.CategoryAccess(False, plan_rules.plan_for_rank(minimum))


def build(params, *, organization=None, public: bool = False) -> dict:
    """The template context for one catalogue page, from the query string ``params``."""
    every = services.submittable_versions()
    counts: dict = {}
    categories = []
    creator_total = 0
    for version in every:
        category = version.definition.category
        if category.pk not in counts:
            categories.append(category)
            counts[category.pk] = 0
        counts[category.pk] += 1
        creator_total += version.definition.creator
    query = (params.get("q") or "").strip()[:80]
    chosen = (params.get("category") or "")[:80]
    industry = (params.get("industry") or "").upper()
    industry = industry if industry in INDUSTRIES else ""
    creator = params.get("view") == "creator"
    versions = every
    if creator:
        versions = versions.filter(definition__creator=True)
        chosen = ""
    elif chosen:
        versions = versions.filter(definition__category__slug=chosen)
    if industry:
        versions = versions.filter(definition__industries__contains=industry)
    if query:
        versions = versions.filter(Q(definition__name__icontains=query) | Q(definition__code__icontains=query)
                                   | Q(definition__subtype__icontains=query) | Q(scope_summary__icontains=query)
                                   | Q(deliverables_summary__icontains=query)
                                   | Q(definition__category__name__icontains=query))
    filtered = bool(chosen or query or industry or creator)
    access = {}
    for category in categories:
        access[category.pk] = public_access(category) if public else \
            plan_rules.category_access(organization, category)
        category.access = access[category.pk]
        category.count = counts[category.pk]
    versions = list(versions.order_by("definition__category__sort_order", "definition__code", "definition__name")) \
        if filtered else []
    for version in versions:
        version.access = access[version.definition.category_id]
    guide = guide_data.industry(industry) if industry and not (chosen or query or creator) else None
    guide_steps, creator_groups, guide_categories = [], [], []
    if guide or creator:
        by_code = {}
        for version in every.filter(definition__code__gt=""):
            version.access = access[version.definition.category_id]
            by_code[version.definition.code] = version
        if guide:
            guide_steps = guide_data.resolve(guide["steps"], by_code, "step")
            # Below the guide, link to every task for this industry by category, not one long list.
            per_category: dict = {}
            for version in versions:
                per_category.setdefault(version.definition.category_id, 0)
                per_category[version.definition.category_id] += 1
            guide_categories = [(c, per_category[c.pk]) for c in categories if c.pk in per_category]
        if creator:
            creator_groups = [g for g in guide_data.resolve(guide_data.creator_entries(), by_code, "entry")
                              if g["versions"]]
    keep = params.copy()
    keep.pop("industry", None)
    return {
        "versions": versions, "categories": categories, "chosen": chosen, "query": query,
        "filtered": filtered, "total": every.count(), "industry": industry, "industries": INDUSTRIES,
        "creator": creator, "creator_total": creator_total, "keep": keep.urlencode(),
        "industry_label": INDUSTRIES.get(industry, ""),
        "guides": guide_data.industries(), "guide": guide, "guide_steps": guide_steps,
        "creator_groups": creator_groups, "guide_categories": guide_categories,
        "public": public,
        "card_template": "banda/public/_service_card.html" if public else "banda/app/_service_card.html",
    }
