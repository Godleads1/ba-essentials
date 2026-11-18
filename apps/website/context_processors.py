from __future__ import annotations

from .models import MenuPlacement
from .services import menu_pages


def site_menu(request):
    """Published pages for the public header. The query only runs if a template uses it."""
    return {"header_pages": menu_pages(MenuPlacement.HEADER)}
