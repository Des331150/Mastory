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

    The split itself is ``all_blocks``'s: a construct that spans a blank line -
    a loose list, a fenced code block with blank lines in it - is one paragraph,
    and slides whose material predates that still point at the paragraph they
    were stored for, by the renumbering migration.
    """
    return [block for block in all_blocks(markdown) if plain_text(block)]


_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})", re.MULTILINE)
_LIST_ITEM = re.compile(r"^\s{0,3}(?:([-*+])\s|\d{1,9}\. )")
_THRESH = re.compile(r"^\s{0,3}([-*+])(?:\s*\1){2,}$")


def _fence_open(text: str) -> bool:
    """Whether ``text`` ends inside a fenced code block.

    Lines are read the way the renderer reads them: a fence opens on 3 or
    more of one character and, for backticks, only when the rest of the line
    holds no backtick - a line like ```python``` is a fence is a paragraph to
    the renderer, so it opens nothing here either. Inside an open fence only
    a bare line of that same character closes it, at least as long as the run
    that opened it, and anything else is content.
    """
    opened = ""
    for line in text.split("\n"):
        stripped = line.strip()
        if opened:
            if len(stripped) >= len(opened) and set(stripped) == {opened[0]}:
                opened = ""
        else:
            found = _FENCE.match(line)
            if found is None:
                continue
            run = found.group(1)
            if run[0] != "`" or "`" not in stripped[len(run):]:
                opened = run
    return opened != ""


def _is_list_item(chunk: str) -> str | None:
    """The marker ``chunk`` opens with, as a string like ``-`` or ``1.``, or None.

    Only what the renderer treats as a list counts: the ``1)`` spelling
    python-markdown reads as prose is not a list, a thematic break is not a
    list however much ``- - -`` looks like one, and neither is a chunk that
    starts with anything other than a marker on its first line: a paragraph,
    a heading, an indented block. The marker is what merges are judged on -
    two lists of different kinds that happen to sit beside each other are
    two paragraphs, not one.
    """
    stripped = chunk.lstrip()
    line = stripped.split("\n", 1)[0]
    if _THRESH.match(stripped) is not None:
        return None
    found = _LIST_ITEM.match(line)
    if found is None:
        return None
    bullet = found.group(1)
    return bullet if bullet else "1."


def all_blocks(markdown: str) -> list[str]:
    """Every block of a slide, including the ones holding no words.

    A block is a Markdown construct, not merely a run between blank lines: a
    loose list spans the blank lines between its items and a fenced code block
    can hold blank lines of its own, and either would be broken - the list into
    several lists, the fences into prose - by splitting only where the blank
    line is. So chunks that belong together are joined: a chunk inside an open
    fence belongs to the block the fence opened in, and a chunk that opens
    with the same list marker as the list the block before it started joins
    that list, as does an indented continuation line. Chunks of two different
    lists stay apart, because a bullet list and a numbered list that happen to
    sit beside each other are not one paragraph.

    What the page renders, which is more than what a question can cite: a
    diagram on a page of its own is something the student reads and something
    this has to keep showing them.
    """
    chunks = markdown.split("\n\n")
    merged: list[str] = []
    for chunk in chunks:
        marker = _is_list_item(chunk)
        leading = _is_list_item(merged[-1]) if merged else None
        inside_fence = _fence_open(merged[-1]) if merged else False
        same_list = marker is not None and marker == leading
        # A blank-line continuation of a list item: the renderer needs the
        # indented lines delivered with the list to see which item they
        # belong to. Different markers stay apart: two lists that happen to
        # sit beside each other are two paragraphs, not one.
        continues = (
            inside_fence
            or same_list
            or (marker is None and leading is not None and chunk[:1] in (" ", "\t"))
        )
        if merged and continues:
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
        else:
            merged.append(chunk)
    return merged


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
    slide: Slide, *, image_url: Callable[[str], str], open_url: Callable[[int], str]
) -> str:
    """A slide as HTML, with every paragraph addressable on its own.

    The citation target a wrong answer jumps to. Rendering the whole slide as
    one block would leave a link that lands at the top of the page, which is the
    hunting the citation is meant to end.

    Only the blocks that are spans carry an id, and they carry it at the ordinal
    the ``Span`` row was stored with, so a citation built from that row lands
    here. Anything else - a bare diagram - is rendered without one.

    Each paragraph also carries the event that records the student having been
    at it, fired when it scrolls into view. That is the whole of what the retry
    rule is entitled to know about reading, and asking for more would be a rule
    this page cannot check.
    """
    parts: list[str] = []
    ordinal = -1
    for block in all_blocks(slide.markdown):
        html = render_slide_markdown(block, image_url=image_url)
        if not plain_text(block):
            parts.append(f'<div class="block">{html}</div>')
            continue
        ordinal += 1
        url = open_url(ordinal)
        # No stored paragraph means nothing to record an opening against, and an
        # event pointed at the page itself would have htmx posting the reader's
        # own url on every scroll.
        event = f' hx-post="{url}" hx-trigger="revealed" hx-swap="none"' if url else ""
        parts.append(
            f'<div class="span" id="{slide.span_anchor(ordinal)}"{event}>{html}</div>'
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
    open_url: Callable[[Slide, int], str],
) -> list[Section]:
    """The reading surface: one section per slide, filtered by the query."""
    sections: list[Section] = []
    for slide in slides:
        html = render_spaned_markdown(
            slide,
            image_url=partial(image_url, slide),
            open_url=partial(open_url, slide),
        )
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