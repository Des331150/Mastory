"""Rendering the extracted Markdown as the reading surface.

Markdown gives fast load, real search, and an anchor that a citation can point
at. This module owns the three things that need to agree: the HTML, the search
index text, and the anchors.
"""

import re
from dataclasses import dataclass
from functools import partial
from typing import Callable, Iterable

import markdown as markdown_lib

from material.models import Slide

_MARKDOWN_EXTENSIONS = ["extra", "sane_lists", "nl2br"]

_IMAGE_REF = re.compile(r"!\[(?P<alt>[^\]]*)\]\(image:(?P<name>[^)]+)\)")
_MD_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")
_TAG = re.compile(r"(<[^>]+>)")


@dataclass(frozen=True)
class Section:
    anchor: str
    number: int
    title: str
    html: str
    matched: bool
    excerpt: str


def plain_text(markdown: str) -> str:
    """The slide's text without Markdown syntax, used to search."""
    without_images = _MD_IMAGE.sub("", markdown)
    without_links = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", without_images)
    without_marks = re.sub(r"[*_`#>]", "", without_links)
    return re.sub(r"\s+", " ", without_marks).strip()


def blocks(markdown: str) -> list[str]:
    """A slide's paragraphs, in order, with the ones holding no text dropped.

    One definition of what a paragraph is, shared with extraction: a span is
    stored at ingest time by this split and cited at quiz time by the ordinal it
    lands on, so if the two ever split a slide differently every citation would
    point at the wrong paragraph and nothing would say so. ``Span`` rows are
    numbered over exactly this list.

    The rule is the one extraction has always used and it cannot change without
    renumbering every stored span: a block with no words in it is not a span,
    whatever it holds. A picture with no words is rendered by
    ``render_spaned_markdown`` as a block of its own, which keeps it on the page
    without making it a thing a question can be cited to.
    """
    return [block for block in markdown.split("\n\n") if plain_text(block)]


def all_blocks(markdown: str) -> list[str]:
    """Every block of a slide, including the ones holding no words.

    What the page renders, which is more than what a question can cite: a
    diagram on a page of its own is something the student reads and something
    this has to keep showing them.
    """
    return markdown.split("\n\n")


def render_slide_markdown(
    markdown: str, *, image_url: Callable[[str], str]
) -> str:
    """Markdown to HTML, with extracted images pointing at their own URL."""
    prepared = _IMAGE_REF.sub(
        lambda m: f"![{m['alt']}]({image_url(m['name'])})", markdown
    )
    html: str = markdown_lib.markdown(prepared, extensions=_MARKDOWN_EXTENSIONS)
    return html


def render_spaned_markdown(
    slide: Slide, *, image_url: Callable[[str], str]
) -> str:
    """A slide as HTML, with every paragraph addressable on its own.

    The citation target a wrong answer jumps to. Rendering the whole slide as
    one block would leave a link that lands at the top of the page, which is the
    hunting the citation is meant to end.

    Only the blocks that are spans carry an id, and they carry it at the ordinal
    the ``Span`` row was stored with, so a citation built from that row lands
    here. Anything else - a bare diagram - is rendered without one.
    """
    parts: list[str] = []
    ordinal = -1
    for block in all_blocks(slide.markdown):
        html = render_slide_markdown(block, image_url=image_url)
        if not plain_text(block):
            parts.append(f'<div class="block">{html}</div>')
            continue
        ordinal += 1
        parts.append(
            f'<div class="span" id="{slide.span_anchor(ordinal)}">{html}</div>'
        )
    return "\n".join(parts)


def mark_term(html: str, term: str) -> str:
    """Wrap every case-insensitive match of ``term`` in the text of ``html``.

    Only text between tags is touched, so markup and image URLs survive intact.
    """
    if not term:
        return html
    pattern = re.compile(re.escape(term), re.IGNORECASE)
    parts = _TAG.split(html)
    text_positions = [i for i, part in enumerate(parts) if not part.startswith("<")]
    for i in text_positions:
        parts[i] = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", parts[i])
    return "".join(parts)


def excerpt_for(html: str, term: str, width: int = 220) -> str:
    """A short window of text around the first match, for the results list."""
    text = _TAG.sub(" ", html)
    text = re.sub(r"\s+", " ", text).strip()
    position = text.lower().find(term.lower())
    if position == -1:
        return text[:width]
    start = max(0, position - width // 3)
    return text[start : start + width].strip()


def matches(text: str, term: str) -> bool:
    return bool(term) and term.lower() in text.lower()


def build_sections(
    slides: Iterable[Slide],
    *,
    query: str,
    image_url: Callable[[Slide, str], str],
) -> list[Section]:
    """The reading surface: one section per slide, filtered by the query."""
    sections: list[Section] = []
    for slide in slides:
        html = render_spaned_markdown(slide, image_url=partial(image_url, slide))
        if query:
            html = mark_term(html, query)
        sections.append(
            Section(
                anchor=slide.anchor,
                number=slide.number,
                title=slide.title,
                html=html,
                matched=matches(slide.plain_text, query),
                excerpt=excerpt_for(html, query) if query else "",
            )
        )
    return sections