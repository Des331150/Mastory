"""The topic path: inferring it, and the student correcting it.

Inference is one call to ``material.model`` and nothing else. Everything after
it is the student's: rename, split, merge, reorder, re-weight, add, remove.
Every one of those edits persists, and any of them puts the path back in front
of the student to confirm, because a path they have changed is a path they have
not agreed to.

Weight is derived from the pointer map rather than asked for: a topic covering
more slides, more spans, or slides carrying equations or images is worth more of
the student's week. The model is not asked to weigh anything, so it cannot
invent a weighting.
"""

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from material import model
from material.models import Course, Slide, Topic, TopicPath, TopicSlide
from material.users import current_user_id, owned

logger = logging.getLogger(__name__)

#: How much text is worth one unit of a topic's weight. Weight is a count of
#: study units rather than minutes, so the scheduler can divide it into whatever
#: budget the student has rather than the app guessing an hour count here.
CHARS_PER_UNIT = 400

#: Text that says a slide carries an equation or refers to a figure. Either
#: means a slide takes longer to study than its length alone suggests.
MATH_OR_FIGURE = re.compile(
    r"(\$[^$]+\$|\\frac|\\sum|\\int|=|≈|\^|\bfig(?:ure)?\.?\s*\d)",
    re.IGNORECASE,
)


class Rejected(Exception):
    """An edit the student cannot make, in words they can act on."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Unconfirmed(Rejected):
    """Work that needs a confirmed topic path and does not have one.

    A ``Rejected`` because the student can fix it by confirming, which is the
    whole point: the schedule does not exist yet, so the only way to get one is
    to go and check the topics.
    """


def topic_path(course: Course) -> TopicPath:
    """The course's topic path, created in draft on first use."""
    path, _ = TopicPath.objects.get_or_create(
        user_id=current_user_id(),
        course=course,
        defaults={"state": TopicPath.State.DRAFT},
    )
    return path


def course_slides(course: Course) -> list[Slide]:
    """The course's material in course order, as the student would count it."""
    return course.slides


def heading_of(slide: Slide) -> str:
    """The deck's own heading for this slide, or nothing if it has none.

    A slide's title is its first line whether or not that line was a heading, so
    the two are told apart here rather than assumed from the title. Plenty of
    material has no heading structure at all, and that is exactly the case the
    outline hint has to survive.
    """
    for line in slide.markdown.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if not candidate.startswith("#"):
            return ""
        return candidate.lstrip("#").strip().strip("*").strip()
    return ""


def infer(course: Course) -> None:
    """Infer the course's topic path from its material, replacing what is there.

    One model call, through the one module that makes them. A course whose
    material the model cannot ground produces no topics and leaves the previous
    path alone rather than half-replacing it.
    """
    slides = course_slides(course)
    if not slides:
        raise Rejected("There is no readable material to work out topics from.")
    proposals = model.infer_topics(
        course_title=course.title,
        slides=[
            model.SlideExcerpt(
                index=index,
                title=slide.title,
                text=slide.plain_text,
                heading=heading_of(slide),
            )
            for index, slide in enumerate(slides, start=1)
        ],
    )
    if not proposals:
        raise Rejected(
            "No topics could be worked out from this material. Add them yourself "
            "below."
        )
    by_index = {index: slide for index, slide in enumerate(slides, start=1)}
    with transaction.atomic():
        owned(course.topics.all()).delete()
        for position, proposal in enumerate(proposals):
            topic = Topic.objects.create(
                user_id=current_user_id(),
                course=course,
                title=proposal.title,
                position=position,
                flagged=proposal.flagged,
                flag_note=proposal.flag,
            )
            _point_at(topic, [by_index[index] for index in proposal.slides])
            reweigh(topic)
        topic_path(course)
    logger.info(
        "%s: inferred %d topics over %d slides",
        course.title,
        len(proposals),
        len(slides),
    )


def rename(topic: Topic, title: str) -> None:
    """The student's word for a topic, in place of the model's.

    A topic the student has renamed is one they have read and accepted, so the
    flag asking them to check it comes off.
    """
    clean = title.strip()
    if not clean:
        raise Rejected("Give the topic a name so you can recognise it later.")
    topic.title = clean
    topic.flagged = False
    topic.flag_note = ""
    topic.save(update_fields=["title", "flagged", "flag_note"])
    _await_confirmation(topic.course)


def reweigh(topic: Topic, weight: int | None = None) -> None:
    """How much of the student's week this topic is worth.

    Derived from the pointer map when the student has not said otherwise: each
    slide is a unit, each few hundred words of it is another, and a slide
    carrying an equation or a figure is worth more than one of plain prose.
    """
    if weight is not None:
        topic.weight = max(1, weight)
    else:
        slides = topic.slides
        text = " ".join(slide.plain_text for slide in slides)
        topic.weight = max(
            1,
            len(slides)
            + len(text) // CHARS_PER_UNIT
            + sum(1 for slide in slides if MATH_OR_FIGURE.search(slide.plain_text)),
        )
    topic.save(update_fields=["weight"])
    _await_confirmation(topic.course)


def split(topic: Topic, after: Slide, title: str) -> Topic:
    """One topic becomes two, split just after the slide the student named.

    The split falls on a slide boundary rather than through a slide, because a
    topic's slides are its pointer map and half a slide is not something a
    citation can point at. The new topic takes everything from ``after`` onwards
    and sits directly after the one it came from.
    """
    slides = topic.slides
    if after.pk not in {slide.pk for slide in slides}:
        raise Rejected("Split after one of this topic's own slides.")
    head = [slide.pk for slide in slides].index(after.pk) + 1
    tail = slides[head:]
    if not tail:
        raise Rejected("That is the last slide of this topic; there is nothing after it.")
    course = topic.course
    with transaction.atomic():
        _make_room(course, topic.position)
        _point_at(topic, slides[:head])
        fresh = Topic.objects.create(
            user_id=current_user_id(),
            course=course,
            title=title.strip() or f"{topic.title} (part 2)",
            position=topic.position + 1,
        )
        _point_at(fresh, tail)
        reweigh(topic)
        reweigh(fresh)
    _await_confirmation(course)
    return fresh


def merge(first: Topic, second: Topic) -> Topic:
    """Two topics that turn out to be one.

    The survivor is the earlier one, keeps the title the student chose for it,
    and takes every slide of both, so a merged topic can point at slides from
    either side of what used to be the boundary.
    """
    if first.pk == second.pk:
        raise Rejected("Pick two different topics to merge.")
    if first.position > second.position:
        first, second = second, first
    seen = {slide.pk for slide in first.slides}
    extra = [slide for slide in second.slides if slide.pk not in seen]
    with transaction.atomic():
        _point_at(first, first.slides + extra)
        second.delete()
        _renumber(first.course)
        reweigh(first)
    _await_confirmation(first.course)
    return first


def reorder(course: Course, ordered: Sequence[int]) -> None:
    """Put the course's topics in the order the student gave."""
    topics = {topic.pk: topic for topic in owned(course.topics.all())}
    if sorted(ordered) != sorted(topics):
        raise Rejected("That order does not name every topic of this course.")
    _apply_order(topics, ordered)
    _await_confirmation(course)


def _apply_order(topics: dict[int, Topic], ordered: Sequence[int]) -> None:
    """Write new positions without ever two topics holding the same one.

    Positions are unique per course, so a swap cannot be written as two updates:
    the first would land on a number the other still holds. Every topic is
    parked above the highest position in use first, then given its final number.
    Parking starts above the current maximum rather than at the number of topics
    because a course can arrive here with gaps in it - after a topic is removed,
    say - and a gap is exactly the number a parked topic would land on.
    """
    highest = max((topic.position for topic in topics.values()), default=-1)
    for offset, pk in enumerate(ordered):
        topic = topics[pk]
        topic.position = highest + 1 + offset
        topic.save(update_fields=["position"])
    for position, pk in enumerate(ordered):
        topic = topics[pk]
        topic.position = position
        topic.save(update_fields=["position"])


def add(course: Course, title: str, slides: Iterable[Slide] = ()) -> Topic:
    """A topic the student knows is in the course and no inference found.

    Appended rather than slotted in: the student cannot say where a topic they
    are adding belongs, and guessing where it goes would be worse than last.
    """
    clean = title.strip()
    if not clean:
        raise Rejected("Give the topic a name so you can recognise it later.")
    with transaction.atomic():
        topic = Topic.objects.create(
            user_id=current_user_id(),
            course=course,
            title=clean,
            position=owned(course.topics.all()).count(),
        )
        _point_at(topic, list(slides))
        reweigh(topic)
    _await_confirmation(course)
    return topic


def remove(course: Course, topic: Topic) -> None:
    """A topic that will not be examined, so no session is spent on it."""
    with transaction.atomic():
        topic.delete()
        _renumber(course)
    _await_confirmation(course)


def confirm(course: Course) -> None:
    """The student has checked the path, so a schedule may be built on it."""
    if not owned(course.topics.all()).exists():
        raise Rejected("There are no topics to confirm yet.")
    path = topic_path(course)
    path.state = TopicPath.State.CONFIRMED
    path.confirmed_at = timezone.now()
    path.save(update_fields=["state", "confirmed_at"])
    logger.info("%s: topic path confirmed", course.title)


def is_confirmed(course: Course) -> bool:
    """Whether the student has confirmed this course's topic path."""
    path = TopicPath.objects.filter(user_id=current_user_id(), course=course).first()
    return path is not None and path.state == TopicPath.State.CONFIRMED


def require_confirmation(course: Course) -> list[Topic]:
    """The topics a schedule may be built on, or a refusal if there are none yet.

    The single gate a schedule has to come through. A wrong topic path the
    student fixed in thirty seconds is acceptable; one discovered during finals
    is fatal, so generating a schedule without asking is the one failure this
    product cannot have. Built now, before there is a schedule to gate, so the
    later ticket inherits the rule instead of deciding whether it needs one.
    """
    if not is_confirmed(course):
        raise Unconfirmed("Confirm the topic path before building a schedule.")
    topics = list(owned(course.topics.all()))
    if not topics:
        raise Unconfirmed("There are no topics to build a schedule on.")
    return topics


def _await_confirmation(course: Course) -> None:
    """Put the path back in front of the student after it has been changed.

    Confirming is what says "this is right". A path edited since is not the path
    they said was right, so the confirmation is cleared and they confirm again.
    """
    path = topic_path(course)
    if path.state != TopicPath.State.DRAFT:
        path.state = TopicPath.State.DRAFT
        path.confirmed_at = None
        path.save(update_fields=["state", "confirmed_at"])


def _point_at(topic: Topic, slides: Iterable[Slide]) -> None:
    """Make a topic's pointer map exactly these slides.

    Rows are added and removed rather than the map rewritten, so a slide shared
    by two topics keeps pointing at both.
    """
    wanted = {slide.pk for slide in slides}
    for pointer in list(topic.pointers.all()):
        if pointer.slide_id not in wanted:
            pointer.delete()
    present = {pointer.slide_id for pointer in topic.pointers.all()}
    for slide in slides:
        if slide.pk not in present:
            TopicSlide.objects.create(
                user_id=topic.user_id, topic=topic, slide=slide
            )


def _renumber(course: Course) -> None:
    """Number the course's topics 0..n-1, keeping the order they are in."""
    current = list(owned(course.topics.all()))
    _apply_order({topic.pk: topic for topic in current}, [t.pk for t in current])


def _make_room(course: Course, position: int) -> None:
    """Push every topic after ``position`` down one, opening a gap to fill.

    Parked above the top first, for the same reason as ``_apply_order``: two
    topics cannot briefly share a position.
    """
    topics = list(owned(course.topics.all()))
    highest = max((topic.position for topic in topics), default=-1)
    for offset, topic in enumerate(topics):
        if topic.position > position:
            topic.position = highest + 1 + offset
            topic.save(update_fields=["position"])
    for topic in topics:
        if topic.position > highest:
            topic.position -= highest + 1
            topic.save(update_fields=["position"])