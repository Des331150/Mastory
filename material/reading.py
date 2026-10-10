"""Whether the student has been back to the material: the record behind the
quiz's retry rule.

One question is answered here and it is asked often - has this student opened
this paragraph *since they were sent to it*? - so it is answered in one place
rather than by every caller reaching for the table and re-deciding what
"opened" means.

Two things earn their place:

- **Presence is enough, and nothing more is asked.** Opening a paragraph is
  recorded. Reading it is not measured, because a rule that can be satisfied
  without understanding still works, and a rule that requires understanding
  cannot be checked by a server at all.
- **Since, not ever.** A paragraph opened last week does not discharge an
  opening owed this afternoon. Reading the topic is what the product asks a
  student to do before the quiz, so an all-time record would mean the rule was
  already satisfied before it ever applied, and the one thing this module exists
  to provide would be a rule that never fires.

The recording takes a span and nothing else: no dwell time, no scroll distance,
so no later rule can grow one out of it by accident.
"""

import logging
from collections.abc import Iterable
from datetime import datetime

from django.utils import timezone

from material.models import SectionOpen, Span
from material.users import owned

logger = logging.getLogger(__name__)


def record_open(user_id: int, span: Span) -> SectionOpen:
    """One paragraph opened, recording that they have been there.

    One row per paragraph, stamped with the *latest* time they were there rather
    than the first. That is what the retry rule asks: not whether they have ever
    read it, but whether they have been back since they were sent. Keeping the
    first moment instead would mean a student who opened a paragraph before
    failing, went back to it afterwards, and was held for exactly that paragraph
    could never be let through - held by their own earlier visit.

    Logged every time, because whether a student was sent back to their material
    and went is the earliest honest sign that the citation is doing anything,
    and none of it can be backfilled.
    """
    row, created = SectionOpen.objects.get_or_create(user_id=user_id, span=span)
    if not created:
        # ``auto_now_add`` only stamps on insert, so a return visit has to be
        # written by hand or the row keeps the moment it was first opened.
        row.opened_at = timezone.now()
        row.save(update_fields=["opened_at"])
    logger.info("opened span %s", span.pk)
    return row


def unopened_since(
    user_id: int, spans: Iterable[Span], *, since: datetime
) -> tuple[Span, ...]:
    """Which of these paragraphs this student has not opened since ``since``.

    Returned with the slide and file behind each one already fetched: the
    refusal names every paragraph by its citation, which is two more lookups per
    paragraph on a page the student is already waiting to read.

    Paragraphs belonging to another student drop out of the answer rather than
    counting as unopened. An answer is never sent to somebody else's paragraph,
    and a caller holding one has a bug rather than a student to protect -
    counting it as owed would refuse a retry over a page that can never open.
    """
    wanted = [span for span in spans if span.user_id == user_id]
    if not wanted:
        return ()
    seen = set(
        owned(
            SectionOpen.objects.filter(span__in=wanted, opened_at__gte=since)
        ).values_list("span_id", flat=True)
    )
    return tuple(
        span
        for span in owned(
            Span.objects.filter(pk__in=[span.pk for span in wanted])
        ).select_related("slide__source_file")
        if span.pk not in seen
    )