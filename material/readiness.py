"""The readiness report: the end of the course, and the figure it refuses to print.

This is the artefact a student screenshots and shows a course-mate, so everything
here is a claim the student may act on. Three decisions earn their place:

- **The figure is a weighted mean of the topics that have been weighed, and only
  printed when enough of the course has been.** Each topic counts for as much as
  its own ``weight`` says - the same weight the week's minutes were built from -
  because a course does not examine its two-slide topic and its twenty-slide one
  equally. Unweighed topics are left out of the mean rather than counted as zero:
  zero is a claim that the student knows nothing, and the absence of a sitting is
  not that claim. What the mean is not made of is stated on the page instead.

- **Too little data is refused in words, not in a footnote.** Below
  ``MEASURED_SHARE`` of the course, ``Readiness.measurable`` is false and the page
  prints the refusal and the shortfall. A partial-data percentage is worse than
  no number, because a student will act on it: they will revise four topics and
  believe the figure describes the course.

- **The weakest topics are ranked whatever the coverage.** The ranking is the part
  of the report a student can act on today, and it does not get withheld because
  the mean is refused - the ranking is read off the topics that have been sat,
  and naming them costs nothing.

Everything is read, never stored. The report is a reading of the attempt rows and
the topic rows, so it cannot drift from the record it describes, and it is as
current as the page that drew it.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from math import ceil
from typing import Sequence

from django.utils import timezone

from material import mastery
from material.models import Attempt, Course, Topic
from material.users import owned

#: How much of the course has to have been weighed before a readiness figure is
#: printed at all.
#:
#: Half, and no more clever than that: a mean over the topics a student happened
#: to quiz describes the topics, not the course, and half is the line at which it
#: stops being a sample that could have gone badly. Below it the report refuses,
#: which is also why a student with one topic of twenty sat sees a sentence rather
#: than a number - a one-of-twenty figure of 80% would read as "you are ready".
MEASURED_SHARE = 0.5

#: How many topics the report names as the ones to revise first.
#:
#: Three, because the ranking exists to be finished in one sitting. A list of
#: twenty is the standing table above it and would be read as nothing in
#: particular.
WEAKEST = 3


@dataclass(frozen=True)
class TopicReading:
    """One topic in the report: what it is worth, and how much sitting says so.

    The sittings are on the row rather than behind it because one sitting and
    five sittings are not the same claim about what a student knows, and the
    mastery number alone cannot say which it is.
    """

    topic: Topic
    mastery: mastery.Mastery
    first_on: date | None
    last_on: date | None

    @property
    def sittings(self) -> int:
        """How many times the student has sat this topic's quiz."""
        return self.mastery.sittings

    @property
    def measured(self) -> bool:
        """Whether anything has been weighed on this topic."""
        return self.mastery.measured

    @property
    def percent(self) -> int:
        return self.mastery.percent


@dataclass(frozen=True)
class StudyDay:
    """One day the student worked, and what they did on it.

    Sittings and finished topics together, because either alone is a partial
    picture: a student who read a week and sat nothing, or sat a quiz on a topic
    they have not marked done, has studied on a day that one of the two columns
    would leave out.
    """

    on: date
    sittings: int
    finished: int


@dataclass(frozen=True)
class Readiness:
    """Course readiness: the weighted mean of what has been weighed, or nothing.

    ``value`` is ``None`` when the course has not been weighed enough for the
    mean to mean anything, for the same reason ``mastery.Mastery.value`` is: the
    absence of a figure is the finding, and printing one anyway would be a claim
    the data does not support.
    """

    value: float | None
    covered: int
    total: int

    @property
    def measurable(self) -> bool:
        """Whether enough of the course has been weighed for a figure to exist."""
        return self.value is not None

    @property
    def percent(self) -> int:
        """Readiness as a whole number, or zero where there is nothing to read."""
        return round(self.value * 100) if self.value is not None else 0

    @property
    def needed(self) -> int:
        """How many topics have to be weighed before this figure appears."""
        return ceil(MEASURED_SHARE * self.total)

    @property
    def reason(self) -> str:
        """The refusal, said in the numbers, or nothing when there is a figure.

        Named on the page rather than hidden behind a missing number: a student
        who cannot see why there is no figure will take the absence for a bug, and
        a student who sees it can go and sit the quizzes it asks for.
        """
        if self.measurable:
            return ""
        if not self.total:
            return (
                "This course has no topics on it, so there is nothing to weigh "
                "and no readiness figure to print."
            )
        return (
            f"You have weighed {self.covered} of this course's {self.total} "
            f"topics, and readiness needs at least {self.needed} of them before "
            f"it means anything. A mean over the topics you happened to quiz "
            f"would be a number about your evenings rather than about your "
            f"course, so this report is not printing one. Sit the quizzes on the "
            f"topics below and it will."
        )


@dataclass(frozen=True)
class Report:
    """The whole report: every topic, the days studied, and the figure or refusal."""

    course: Course
    topics: tuple[TopicReading, ...]
    readiness: Readiness
    weakest: tuple[TopicReading, ...]
    days: tuple[StudyDay, ...]

    @property
    def weighed(self) -> int:
        """How many topics have a mastery figure at all."""
        return sum(1 for topic in self.topics if topic.measured)

    @property
    def unweighed(self) -> tuple[TopicReading, ...]:
        """The topics with nothing weighed on them, in course order."""
        return tuple(topic for topic in self.topics if not topic.measured)

    @property
    def days_studied(self) -> int:
        """How many separate days the record says the student worked on."""
        return len(self.days)

    @property
    def unweighed_titles(self) -> str:
        """The unweighed topics named in one line, in course order.

        Named here rather than joined in the template because a report line has to
        read as a sentence, and a template joining dataclass objects would print
        their reprs rather than their titles.
        """
        return ", ".join(reading.topic.title for reading in self.unweighed)


def report(course: Course) -> Report:
    """The readiness report for this course, read from its own record.

    Every topic of the course, in the order the course teaches them, weighed with
    ``material.mastery`` - the same function the plan page and the quiz page read
    a topic with, so the report cannot print a mastery figure that disagrees with
    the one the student was just looking at.
    """
    topics = list(owned(course.topics.all()))
    taken: dict[int, list[Attempt]] = defaultdict(list)
    for attempt in owned(Attempt.objects.filter(topic_id__in=[t.pk for t in topics])):
        taken[attempt.topic_id].append(attempt)
    readings = tuple(
        TopicReading(
            topic=topic,
            mastery=mastery.measure(taken[topic.pk]),
            first_on=_first_day(taken[topic.pk]),
            last_on=_last_day(taken[topic.pk]),
        )
        for topic in topics
    )
    return Report(
        course=course,
        topics=readings,
        readiness=_readiness(readings),
        weakest=_weakest(readings),
        days=_days(readings, taken),
    )


def _readiness(readings: Sequence[TopicReading]) -> Readiness:
    """The weighted mean of the weighed topics, or the refusal of one.

    Weighted by ``Topic.weight`` because the course does not weigh its topics
    equally - it is the same number the week's minutes were built from, so the
    figure and the plan are one reading of the same course. The denominator
    guarded with ``or 1`` as in ``material.planning``: a topic's weight is floored
    at one by the student's own edit, and a report that raised ``ZeroDivisionError``
    over a weight of zero would be worse than one that read such a course oddly.
    """
    weighed = [reading for reading in readings if reading.measured]
    covered, total = len(weighed), len(readings)
    if not weighed or covered < MEASURED_SHARE * total:
        return Readiness(value=None, covered=covered, total=total)
    scale = sum(reading.topic.weight for reading in weighed) or 1
    value = sum(
        (reading.mastery.value or 0.0) * reading.topic.weight for reading in weighed
    ) / scale
    return Readiness(value=value, covered=covered, total=total)


def _weakest(readings: Sequence[TopicReading]) -> tuple[TopicReading, ...]:
    """The topics to revise first: the lowest mastery, three of them.

    Ties are broken by the order the course teaches them rather than by topic id,
    so that two topics the student has equal evidence on are named in the order
    they will meet them, and so that the ranking does not move when an unrelated
    topic is added to the course.

    Only weighed topics are ranked. An unweighed topic has no mastery to be low,
    and calling it weak would be a claim about a topic nobody has been asked about;
    the report lists those separately as the ones that would add to the figure.
    """
    weighed = [reading for reading in readings if reading.measured]
    return tuple(sorted(weighed, key=lambda reading: (reading.mastery.value or 0.0,))[:WEAKEST])


def _days(
    readings: Sequence[TopicReading], taken: dict[int, list[Attempt]]
) -> tuple[StudyDay, ...]:
    """Every day the record says the student worked, earliest first.

    Read from the same two places the rest of the report is read - the sittings
    they took and the topics they finished - so that the dates on this page are
    the dates the plan page logged, not a second opinion about them.

    A sitting is dated by its own timestamp in the server's local day rather than
    in UTC, which is the day the student sat it in: ``planning.today`` answers the
    same question for the day a thing is marked, and a study log that disagreed
    with the plan about which day it was would be the sort of pair of answers
    this product keeps refusing to print.
    """
    sittings: dict[date, int] = defaultdict(int)
    finished: dict[date, int] = defaultdict(int)
    for topic_id, attempts in taken.items():
        for attempt in attempts:
            sittings[_local(attempt)] += 1
    for reading in readings:
        if reading.topic.completed_on is not None:
            finished[reading.topic.completed_on] += 1
    return tuple(
        StudyDay(on=on, sittings=sittings[on], finished=finished[on])
        for on in sorted({*sittings, *finished})
    )


def _local(attempt: Attempt) -> date:
    """The day the student sat this quiz, as their own day and not UTC's."""
    return timezone.localtime(attempt.taken_at).date()


def _first_day(attempts: Sequence[Attempt]) -> date | None:
    """The day the first of these sittings was taken, or nothing."""
    return _local(attempts[0]) if attempts else None


def _last_day(attempts: Sequence[Attempt]) -> date | None:
    """The day the most recent of these sittings was taken, or nothing."""
    return _local(attempts[-1]) if attempts else None