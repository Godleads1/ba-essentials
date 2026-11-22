"""Simple, safe formatting for website pages.

Editors write plain text with a few marks:

    ## Heading          ### Smaller heading
    **bold**            *italic*
    [link text](https://example.com)   [link](/services/)   [email](mailto:hello@example.com)
    - bullet item       1. numbered item
    ![description](image:<id>)   an uploaded image, on a line of its own

A blank line starts a new paragraph. Everything is HTML-escaped first and only
the tags above are added back, so no HTML, script or style an editor types can
reach the page. Links allow https, http, mailto, tel and paths on this site.
"""
from __future__ import annotations

import re

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

_HEADING = re.compile(r"^(#{1,3})\s+(.+)$")
_BULLET = re.compile(r"^[-*]\s+(.+)$")
_IMAGE = re.compile(r"^!\[([^\[\]\n]{0,200})\]\(image:([0-9a-fA-F-]{36})\)$")
_IMAGE_REF = re.compile(r"image:([0-9a-fA-F-]{36})")
_NUMBER = re.compile(r"^\d{1,3}[.)]\s+(.+)$")
# Applied to text that is already escaped, so it can only ever see &lt; &gt; &quot; etc.
_LINK = re.compile(r"\[([^\[\]\n]{1,200})\]\(([^()\s]{1,500})\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")
_SAFE_SCHEME = re.compile(r"^(https?://[^/\s]|mailto:[^\s]|tel:\+?[\d\-\s]{3,})", re.IGNORECASE)


def safe_link(url: str) -> str | None:
    """The link target if it is allowed, else None. `url` is the escaped text."""
    if _SAFE_SCHEME.match(url):
        return url
    # A path on this site: one leading slash, never // or /\ (which browsers treat as another site).
    if url.startswith("/") and not url.startswith(("//", "/\\")):
        return url
    return None


def _emphasis(text: str) -> str:
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return _ITALIC.sub(r"<em>\1</em>", text)


def _inline(text: str) -> str:
    # Links are set aside first so bold/italic marks can never land inside an href.
    text = text.replace("\x00", "")
    links: list[str] = []

    def link(match: re.Match) -> str:
        label, url = _emphasis(match.group(1)), match.group(2)
        target = safe_link(url)
        if target is None:
            links.append(label)  # keep the words, drop the unsafe link
        else:
            external = target.lower().startswith(("http://", "https://"))
            rel = ' rel="noopener noreferrer"' if external else ""
            links.append(f'<a href="{target}"{rel}>{label}</a>')
        return f"\x00{len(links) - 1}\x00"

    text = _emphasis(_LINK.sub(link, text))
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], text)


def _list(lines: list[str], pattern: re.Pattern, tag: str) -> str:
    items = "".join(f"<li>{_inline(pattern.match(line).group(1))}</li>" for line in lines)
    return f"<{tag}>{items}</{tag}>"


def image_ids(body: str) -> set[str]:
    return {m.lower() for m in _IMAGE_REF.findall(body or "")}


def _image(match: re.Match, images: dict) -> str:
    image = images.get(match.group(2).lower())
    if image is None:
        return ""  # deleted or unknown: show nothing rather than a broken image
    from django.urls import reverse

    alt = match.group(1).strip() or escape(image.alt_text)  # group(1) is already escaped text
    src = reverse("website-image", args=[image.pk])
    return (f'<figure><img src="{src}" alt="{alt}" width="{int(image.width)}" height="{int(image.height)}" '
            'loading="lazy" decoding="async"></figure>')


def render(body: str, images: dict | None = None) -> SafeString:
    """`images` maps image id (lower-case string) to PageImage, for `![…](image:<id>)` lines."""
    images = images or {}
    html: list[str] = []
    text = escape((body or "").replace("\r\n", "\n").replace("\r", "\n"))
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if len(lines) == 1 and (picture := _IMAGE.match(lines[0])):
            if figure := _image(picture, images):
                html.append(figure)
        elif len(lines) == 1 and (heading := _HEADING.match(lines[0])):
            level = 2 if len(heading.group(1)) <= 2 else 3  # the page title is the only h1
            html.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif all(_BULLET.match(line) for line in lines):
            html.append(_list(lines, _BULLET, "ul"))
        elif all(_NUMBER.match(line) for line in lines):
            html.append(_list(lines, _NUMBER, "ol"))
        else:
            html.append("<p>" + "<br>".join(_inline(line) for line in lines) + "</p>")
    # Safe: every piece is escaped text or one of the fixed tags above (tests/test_website_editor.py).
    return mark_safe("\n".join(html))


def render_inline(text: str) -> SafeString:
    """Text that sits inside an existing heading or paragraph: **bold**, *italic*, links, line breaks.

    No headings, lists or images, so the page layout can't change. Same escaping as render().
    """
    lines = escape((text or "").replace("\r\n", "\n").replace("\r", "\n")).strip().split("\n")
    # Safe: escaped text plus the fixed inline tags from _inline().
    return mark_safe("<br>".join(_inline(line.strip()) for line in lines if line.strip()))
