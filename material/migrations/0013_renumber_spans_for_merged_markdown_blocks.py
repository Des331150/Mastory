"""Renumber stored spans over the split that keeps markdown whole.

The paragraph split used to cut a slide at every blank line. A loose list and
a fenced code block that spans a blank line were therefore cut apart - the
list into several spans, the code block into its fences - and every slide
holding one of those constructs carries Span rows numbered over the split
that damaged them. Renumbering is the release note in the ticket that found
the damage (Mastory issue 15): the rows keep pointing at the paragraph they
were stored for, which after the fix means the paragraph the new split makes.

The rows are updated in place, in two passes, so nothing that cites a
paragraph the split kept moves rows: its questions, its missed records and
its section opens - the retry rule's own input - keep pointing at the same
row and only ride the ordinal and text change. (slide, ordinal) is unique, so
the first pass lifts every ordinal above anything the second pass hands out.
Only rows whose paragraph the split no longer names - a loose list's separate
items, welded into the list's one paragraph - are deleted, with whatever
cites them: nothing can keep saying "paragraph 4 as it was" when the split's
paragraph 3 now holds the text of the items as one span.

A slide whose material contains neither construct splits identically before
and after, so its rows are left alone: same count, same texts, same ordinals.
"""

from collections.abc import Sequence

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import F

from material.render import all_blocks, plain_text


def spans_of(markdown: str) -> list[str]:
    """The slide's paragraphs as the new split numbers them.

    The same computation ingest does when it stores a span, so the renumbered
    rows are what a fresh upload of these same bytes would have stored.
    """
    return [plain_text(block) for block in all_blocks(markdown) if plain_text(block)]


# Position into ``stored``: which raw rows each paragraph of the new split is
# made of. The split only ever merges consecutive chunks, so each paragraph
# maps onto a run of consecutive rows whose texts, joined the way the blank
# line joins the chunks, are the paragraph's stored text. A run of one row
# names a paragraph the split left alone.
Mapping = Sequence[Sequence[int]]


def _mapping(stored: Sequence[str], now: Sequence[str]) -> tuple[Mapping, int]:
    """The run of old rows behind each new paragraph, and the next unused row."""
    mapping: list[list[int]] = []
    row = 0
    for text in now:
        run: list[int] = []
        while row < len(stored):
            run.append(row)
            row += 1
            if " ".join(stored[position] for position in run) == text:
                break
        mapping.append(run)
    return mapping, row


def renumber(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Slide = apps.get_model("material", "Slide")
    Span = apps.get_model("material", "Span")
    for slide in Slide.objects.all():
        rows = list(slide.spans.order_by("id"))
        stored = [row.text for row in rows]
        now = spans_of(slide.markdown)
        if stored == now:
            continue
        mapping, consumed = _mapping(stored, now)
        # Above anything the second pass writes and above every ordinal the
        # slide holds right now, so renumbering never trips the one
        # (slide, ordinal) a slide allows: SQLite checks the constraint per
        # row as it goes, so the lift has to land clear of live values.
        slide.spans.update(ordinal=F("ordinal") + 1_000_000 + len(now))
        dropped: list[int] = []
        for ordinal, positions in enumerate(mapping):
            survivor = rows[positions[0]]
            Span.objects.filter(pk=survivor.pk).update(
                ordinal=ordinal, text=now[ordinal]
            )
            dropped.extend(rows[position].pk for position in positions[1:])
        # Rows past what the paragraphs account for cannot be pointed anywhere.
        dropped.extend(row.pk for row in rows[consumed:])
        Span.objects.filter(pk__in=dropped).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("material", "0012_spend"),
    ]

    operations = [
        migrations.RunPython(renumber, reverse_code=migrations.RunPython.noop),
    ]
