from __future__ import annotations

from django import template

from apps.website import markup

register = template.Library()


@register.filter
def page_markup(body: str):
    """Render a page body with the safe formatting in apps/website/markup.py."""
    return markup.render(body)
