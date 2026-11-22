"""Editable text for the home page.

The design stays in the template; website staff change the words. Every field
has a default (the wording the page shipped with), so the page reads the same
until a version is published, and goes back to it if publishing is undone.
Prices and plan details are not here: they come from the billing plans, so the
home page can never show a price clients aren't charged.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.db import transaction
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.audit import services as audit
from apps.core.exceptions import ValidationFailure

from . import markup

LINE = "line"  # one line, plain text (headings, labels, buttons)
PLAIN = "plain"  # a paragraph shown as typed, no formatting (text inside a link, such as the service cards)
TEXT = "text"  # paragraph: **bold**, *italic*, [links](/services/), line breaks
MAX_FAQS = 20


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str
    default: str
    max_length: int = 120
    help: str = ""


def _line(key, label, default, max_length=120, help=""):
    return Field(key, label, LINE, default, max_length, help)


def _text(key, label, default, max_length=600, help=""):
    return Field(key, label, TEXT, default, max_length, help)


def _plain(key, label, default, max_length=260, help="Plain text: the whole card is already a link."):
    return Field(key, label, PLAIN, default, max_length, help)


SECTIONS: list[tuple[str, list[Field]]] = [
    ("Search and link previews", [
        _line("seo_title", "Page title (browser tab and Google)", "banda — B & A Essentials | Beyond your AI", 70,
              "Google shows about 60 characters. Put the most important words first."),
        _line("seo_description", "Search description",
              "Business support for small businesses and SMEs in Nigeria: admin, legal review, operations and "
              "tax readiness, done by people and checked before delivery.", 160,
              "Shown under the title in Google and in link previews. Up to 160 characters."),
    ]),
    ("Top of the page", [
        _line("hero_eyebrow", "Small label above the headline", "YOUR BUSINESS. OUR SUPPORT.", 50),
        _line("hero_title", "Headline", "Get More Done with", 60),
        _line("hero_title_highlight", "Headline, highlighted part", "B & A Essentials", 40,
              "Shown on its own line in blue with an underline."),
        _text("hero_intro", "Introduction", "Reliable, professional and affordable business support services for "
              "small businesses and SMEs in Nigeria.", 300),
        _line("hero_button_primary", "Main button", "Start Today", 30),
        _line("hero_button_secondary", "Second button", "Explore Our Services", 30),
        _line("hero_benefit_1", "Benefit 1", "Save time", 40),
        _line("hero_benefit_2", "Benefit 2", "Get expert support", 40),
        _line("hero_benefit_3", "Benefit 3", "Focus on growth", 40),
    ]),
    ("Services", [
        _line("services_eyebrow", "Small label", "OUR SERVICES", 40),
        _line("services_heading", "Heading", "Everything Your Business Needs", 80),
        _text("services_intro", "Introduction", "From admin support to specialised services, we handle the work so "
              "you can focus on what matters most.", 300),
        _plain("service_business_admin", "Business & Admin", "Proposal and document support, invoicing and payment "
              "follow-up, scheduling and reminders, outreach, filing and data entry.", 260),
        _plain("service_legal_ip_review", "Legal & IP Review", "Contract review and drafting by an enrolled Nigerian "
              "lawyer, CAC business registration, trademarks, and HR and privacy policies.", 260),
        _plain("service_operations", "Operations & Logistics", "Vendor and freelancer coordination, travel and event "
              "logistics, project tracking, and weekly or monthly reports.", 260),
        _plain("service_creator", "Creator Essentials", "For creators and solopreneurs: brand deal filing, sponsor "
              "invoices, collaborator coordination and shoot logistics.", 260),
        _plain("service_tax_readiness", "Tax Readiness", "Income summaries and expense categories organised into an "
              "export package for your own tax expert.", 260),
        _plain("service_specialized_add_ons", "Specialized Add-Ons", "Social media management, bookkeeping, "
              "copywriting, CRM setup and translation, priced separately from your plan.", 260),
        _text("services_note", "Note under the services", "Not sure where your task fits? Send it anyway and a "
              "coordinator will scope it with you.", 200),
        _line("industries_heading", "Industry guides heading", "Browse by industry", 60),
        _text("industries_intro", "Industry guides introduction", "A short guide for businesses like yours: where to "
              "start, then the tasks your industry asks for most.", 200),
    ]),
    ("How it works", [
        _line("how_eyebrow", "Small label", "HOW IT WORKS", 40),
        _line("how_heading", "Heading", "Simple Steps to", 60),
        _line("how_heading_highlight", "Heading, highlighted part", "Get Started", 40, "Shown in orange."),
        _text("how_intro", "Introduction", "Getting the support you need is easy. Follow these simple steps and let "
              "us handle the rest.", 300),
        _line("how_button", "Button", "Get Started", 30),
        _line("how_step_1_title", "Step 1", "Sign Up", 40),
        _text("how_step_1_text", "Step 1 text", "Create your account in minutes.", 160),
        _line("how_step_2_title", "Step 2", "Submit a Task", 40),
        _text("how_step_2_text", "Step 2 text", "Tell us what you need and set your preferences.", 160),
        _line("how_step_3_title", "Step 3", "We Get to Work", 40),
        _text("how_step_3_text", "Step 3 text", "Our team handles the task with care and expertise.", 160),
        _line("how_step_4_title", "Step 4", "You Get Results", 40),
        _text("how_step_4_text", "Step 4 text", "Receive updates and track every step.", 160),
        _line("how_tagline", "Tagline under the steps", "Your Business. Our Priority.", 60),
    ]),
    ("Pricing (headings only; prices come from the billing plans)", [
        _line("pricing_eyebrow", "Small label", "OUR PRICING PLANS", 40),
        _line("pricing_heading", "Heading", "Simple, clear and built for your business", 80),
        _text("pricing_intro", "Introduction", "All plans include access to our secure platform, expert support and "
              "quality assurance.", 300),
        _line("custom_plan_heading", "Custom plan heading", "Need a custom plan?", 60),
        _text("custom_plan_text", "Custom plan text", "If your business has unique requirements or high volume needs, "
              "we'll create a plan that works for you.", 260),
        _line("custom_plan_button", "Custom plan button", "Talk to Our Team", 30),
    ]),
    ("About us", [
        _line("about_eyebrow", "Small label", "ABOUT US", 40),
        _line("about_heading", "Heading", "Real people behind your", 60),
        _line("about_heading_highlight", "Heading, highlighted part", "business support", 40, "Shown in blue."),
        _text("about_paragraph_1", "Paragraph 1", "B & A Essentials helps small businesses and SMEs in Nigeria get "
              "work done. You tell us what you need, and our team handles it with care, from admin and business "
              "documents to legal reviews, business registration, operations and tax records.", 600),
        _text("about_paragraph_2", "Paragraph 2", "Tools can now draft a proposal or outline a plan in seconds. What "
              "they can’t do is take responsibility for the result. We add people to the process: someone to do the "
              "work, someone to check it, and one clear place to see where everything stands.", 600),
        _line("about_tagline", "Tagline", "Beyond your AI.", 40),
        _line("about_point_1_title", "Point 1", "People do the work", 50),
        _text("about_point_1_text", "Point 1 text", "Every request is handled by a person with the right skills, and "
              "checked by a second person before it reaches you.", 200),
        _line("about_point_2_title", "Point 2", "You agree the cost first", 50),
        _text("about_point_2_text", "Point 2 text", "We confirm the scope and price with you. Nothing extra starts "
              "until you approve it.", 200),
        _line("about_point_3_title", "Point 3", "One place to follow it", 50),
        _text("about_point_3_text", "Point 3 text", "Submit requests, share files, see progress and collect finished "
              "work from a single workspace.", 200),
        _line("about_point_4_title", "Point 4", "Your files stay private", 50),
        _text("about_point_4_text", "Point 4 text", "Documents are stored privately and opened only by the people "
              "working on your request.", 200),
    ]),
    ("Questions (headings; the questions themselves are below)", [
        _line("faq_eyebrow", "Small label", "FREQUENTLY ASKED QUESTIONS", 50),
        _line("faq_heading", "Heading", "Still have questions?", 60),
        _text("faq_intro", "Introduction", "Answers to the questions we hear most about how we work and what it "
              "costs.", 300),
    ]),
    ("Closing call to action", [
        _line("cta_heading", "Heading", "Ready to grow your business?", 60),
        _text("cta_text", "Text", "Join B & A Essentials today and experience professional support, built for your "
              "success.", 260),
        _line("cta_button", "Button", "Get Started", 30),
    ]),
]

FIELDS: dict[str, Field] = {f.key: f for _, fields in SECTIONS for f in fields}

DEFAULT_FAQS = [
    {"question": "What kind of tasks can I send you?",
     "answer": "Business and admin work, legal and IP reviews, CAC business registration, operations and logistics, "
               "creator support and tax readiness. Specialist add-ons such as social media management and bookkeeping "
               "are priced separately. You can [browse every task](/services/). If you are not sure whether something "
               "fits, send it and a coordinator will scope it with you before any work starts."},
    {"question": "How are tasks counted?",
     "answer": "Each task in the [service catalogue](/services/) says what it uses. Most use 1 of your monthly tasks, "
               "and bigger pieces of work use 2 or 3. Legal tasks use your legal reviews instead. Before you send a "
               "request we show what it will use and what you have left, and if it needs more than you have, we quote "
               "the extra before any work starts."},
    {"question": "Who does the work?",
     "answer": "A member of our team with the right skills does the work, and a second person checks it before it is "
               "delivered to you."},
    {"question": "Can I change my plan later?",
     "answer": "Yes. Moving up starts straight away, and you pay the difference for the rest of the period. Moving "
               "down starts at your next billing period."},
    {"question": "Is there a setup fee?",
     "answer": "No. You pay the monthly price of your plan and nothing extra to get started."},
    {"question": "What happens if I use more tasks than my plan allows?",
     "answer": "We let you know before you reach your limit. You can upgrade your plan, or we send a quote for the "
               "extra work and only start once you approve it."},
    {"question": "Do you offer refunds?",
     "answer": "Each delivered task includes one round of changes within 2 days, and we fix our own mistakes at no "
               "cost. If we miss a confirmed delivery time by more than 2 business days, we refund that task and give "
               "it back to your allowance. The full rules are in our [Terms of Service](/terms/)."},
    {"question": "Does my plan renew automatically?",
     "answer": "You don’t need to. Plans never renew by themselves and we never charge your card automatically. Before "
               "your plan ends (3 days before for monthly plans, 14 for yearly), we send you a link to pay for the "
               "next period. If you don’t pay, your "
               "plan simply ends: after a grace period of 5 business days your account is paused, and you can pay to "
               "continue at any time. Unused tasks aren’t refunded."},
]
MAX_QUESTION, MAX_ANSWER = 160, 1200


def defaults() -> dict:
    return {**{key: field.default for key, field in FIELDS.items()}, "faqs": [dict(f) for f in DEFAULT_FAQS],
            "share_image": None}


def clean(data: dict) -> dict:
    """Check and normalise submitted content. Unknown keys are dropped; every field is required."""
    content: dict = {}
    for key, field in FIELDS.items():
        value = str(data.get(key) or "").replace("\r\n", "\n").strip()
        if field.kind == LINE:
            value = " ".join(value.split())
        if not value:
            raise ValidationFailure(f"“{field.label}” can't be empty.")
        if len(value) > field.max_length:
            raise ValidationFailure(f"“{field.label}” is too long: keep it to {field.max_length} characters.")
        content[key] = value
    faqs = []
    for item in data.get("faqs") or []:
        question = " ".join(str(item.get("question") or "").split())
        answer = str(item.get("answer") or "").replace("\r\n", "\n").strip()
        if not question and not answer:
            continue
        if not question or not answer:
            raise ValidationFailure("Each question needs both the question and its answer.")
        if len(question) > MAX_QUESTION or len(answer) > MAX_ANSWER:
            raise ValidationFailure(f"Keep questions to {MAX_QUESTION} characters and answers to {MAX_ANSWER}.")
        faqs.append({"question": question, "answer": answer})
    if len(faqs) > MAX_FAQS:
        raise ValidationFailure(f"The page holds up to {MAX_FAQS} questions.")
    content["faqs"] = faqs
    share = data.get("share_image")
    if share:
        try:
            share = str(uuid.UUID(str(share)))  # one spelling, so images.is_live can compare it
        except ValueError as exc:
            raise ValidationFailure("Choose the link-preview image again.") from exc
    content["share_image"] = share or None
    return content


class Copy:
    """The home page words, ready for the template: `{{ home.hero_title }}`, `{% for faq in home.faqs %}`."""

    def __init__(self, content: dict | None = None):
        merged = defaults()
        for key, value in (content or {}).items():
            if key in FIELDS and isinstance(value, str) and value.strip():
                merged[key] = value
        if content and isinstance(content.get("faqs"), list):
            merged["faqs"] = content["faqs"]
        if content:
            merged["share_image"] = content.get("share_image")
        self._content = merged

    def __getattr__(self, key):
        field = FIELDS.get(key)
        if field is None:
            raise AttributeError(key)
        value = self._content[key]
        if field.kind == TEXT:
            return markup.render_inline(value)
        return mark_safe(escape(value))  # LINE and PLAIN: shown exactly as typed

    @property
    def faqs(self):
        return [{"question": mark_safe(escape(f["question"])),
                 "question_text": f["question"],
                 "answer": markup.render_inline(f["answer"]),
                 "answer_text": f["answer"]}
                for f in self._content["faqs"]]

    def plain(self, key: str) -> str:
        """The raw words, for meta tags and structured data (the template escapes them)."""
        return self._content[key]

    @property
    def share_image(self):
        from .models import PageImage

        image_id = self._content.get("share_image")
        return PageImage.objects.filter(pk=image_id).first() if image_id else None


# ---------- versions ----------

def _state():
    from .models import HomePage

    state, _ = HomePage.objects.select_for_update().get_or_create(pk=1)
    return state


def published_copy() -> Copy:
    from .models import HomePage

    state = HomePage.objects.select_related("published_revision").filter(pk=1).first()
    revision = state.published_revision if state else None
    return Copy(revision.content if revision else None)


def current_content() -> dict:
    """What the editor starts from: the newest version, else the defaults."""
    from .models import HomeRevision

    latest = HomeRevision.objects.order_by("-number").first()
    return Copy(latest.content if latest else None)._content


@transaction.atomic
def save_draft(data: dict, *, actor):
    from .models import HomeRevision

    content = clean(data)
    _state()  # lock, so two editors can't take the same number
    number = (HomeRevision.objects.order_by("-number").values_list("number", flat=True).first() or 0) + 1
    revision = HomeRevision.objects.create(number=number, content=content, created_by=actor)
    audit.record(action="website.home_draft_saved", actor=actor, resource_type="website_home",
                 resource_id=str(revision.pk), reason=f"v{number}")
    return revision


@transaction.atomic
def publish(revision, *, actor):
    from apps.accounts.permissions import WEBSITE_PUBLISH, require_perm

    require_perm(actor, WEBSITE_PUBLISH)
    clean(revision.content)  # a version saved under older rules must still pass today's
    state = _state()
    state.published_revision = revision
    state.save(update_fields=["published_revision", "updated_at"])
    audit.record(action="website.home_published", actor=actor, resource_type="website_home",
                 resource_id=str(revision.pk), reason=f"v{revision.number}")
    return state


@transaction.atomic
def use_original(*, actor):
    """Go back to the wording the page shipped with. Versions are kept."""
    from apps.accounts.permissions import WEBSITE_PUBLISH, require_perm

    require_perm(actor, WEBSITE_PUBLISH)
    state = _state()
    state.published_revision = None
    state.save(update_fields=["published_revision", "updated_at"])
    audit.record(action="website.home_reverted", actor=actor, resource_type="website_home", resource_id="1")


def restore(revision, *, actor):
    """A new draft with an earlier version's words; nothing changes on the live page until it is published."""
    return save_draft(revision.content, actor=actor)
