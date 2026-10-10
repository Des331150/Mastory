"""Whether the student has been back to the material: the record behind the
quiz's retry rule.

One question is answered here and it is asked often - has this student opened
this paragraph? - so it is answered in one place rather than by every caller
reaching for the table and re-deciding what "opened" means.

Two things earn their place:

- **Presence is enough, and nothing more is asked.** Opening a paragraph is
  recorded. Reading it is not measured, because a rule that can be satisfied
  without understanding still works, and a rule that requires understanding
  cannot be checked by a server at all.
- **Opening it twice is still opening it.** One row per paragraph, so the
  record cannot be inflated by a student refreshing, and so a paragraph read
  after two failures stays read for the third.

The recording takes a span and nothing else: no dwell time, no scroll distance,
so no later rule can grow one out of it by accident.
"""

from collections.abc import Iterable

from material.models import SectionOpen, Span
from material.users import owned


def record_open(user_id: int, span: Span) -> SectionOpen:
    """One paragraph opened, recorded the first time it is opened.

    Idempotent, because the reading surface fires the event whenever a paragraph
    scrolls into view and a student scrolling back up a page has not opened
    anything new by doing it. The first opening is kept: its moment is the one
    that says when the student went back to the material.
    """
    row, _ = SectionOpen.objects.get_or_create(user_id=user_id, span=span)
    return row


def has_opened(user_id: int, span: Span) -> bool:
    """Whether this student has opened this paragraph at any point."""
    return owned(SectionOpen.objects.filter(user_id=user_id, span=span)).exists()


def unopened(user_id: int, spans: Iterable[Span]) -> tuple[Span, ...]:
    """Which of these paragraphs this student has not opened, in the order given.

    Paragraphs belonging to another student drop out of the answer rather than
    counting as unopened, because an answer is never sent to somebody else's
    paragraph and a caller holding one has a bug rather than a student to
    protect. Counting them as owed would refuse a retry for a page that can
    never be opened.
    """
    wanted = [span for span in spans if span.user_id == user_id]
    if not wanted:
        return ()
    seen = set(
        owned(SectionOpen.objects.filter(span__in=wanted)).values_list("span_id", flat=True)
    )
    return tuple(span for span in wanted if span.pk not in seen)