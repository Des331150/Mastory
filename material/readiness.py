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

- **The gate that refuses it is in the same unit as the figure.** Enough topics
  *and* enough weight, or the report says so. A gate counting only topics would be
  satisfied by the smallest half of the course and would print a confident number
  about the part of a course nobody has measured.

- **Too little data is refused in words, not in a footnote.** Below that bar
  ``Readiness.measurable`` is false and the page prints the refusal and the
  shortfall. A partial-data percentage is worse than no number, because a student
  will act on it: they will revise four topics and believe the figure describes
  the course.

- **The weakest topics are ranked whatever the coverage.** The ranking is the part
  of the report a student can act on today, and it does not get withheld because
  the mean is refused - the ranking is read off the topics that have been sat,
  and naming them costs nothing.

``Attempt.passes`` is not folded into the figure either. It is a second signal
from the same sitting and the report's standing on it is deliberately the one
``material.mastery`` takes: the readiness figure is a mean of mastery, and a
figure that quietly weighted a second signal into it would be a third number that
means neither "how much you know" nor "how much of the right answer you could
argue for". The report shows how many sittings each number rests on, which is what
tells a student whether it is worth acting on.

Everything is read, never stored. The report is a reading of the attempt rows and
the topic rows, so it cannot drift from the record it describes, and it is as
current as the page that drew it.
"""

from collections import defaultdict
from collections.abc import Mapping
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

#: The one word each state of a topic is given, shared with ``mastery.Standing``
#: rather than spelled out per page so that the report and the week cannot name
#: one topic two ways.
COMPLETION = {
    Topic.DONE: "finished",
    Topic.SKIPPED: "skipped",
    Topic.CUT: "cut",
    Topic.PLANNED: "not finished",
}


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

    @property
    def state(self) -> str:
        """Where the plan calls this topic, in the word the plan uses.

        Read through ``mastery.Standing`` rather than off the model's own fields,
        so that the report and the plan cannot name one topic two ways - a page
        that says a topic is cut while the week says it is planned is the sort of
        pair of answers this report exists to prevent.
        """
        return mastery.Standing(topic=self.topic, mastery=self.mastery).state

    @property
    def completion(self) -> str:
        """The one word the table gives this topic's place in the record.

        The same four words the plan's standing column prints, for the same four
        states.
        """
        return COMPLETION[self.state]

    @property
    def summary(self) -> str:
        """The figure and the evidence behind it, in one phrase.

        The sittings are in the phrase rather than in a column of their own
        because the count changes what the number means: one sitting is a
        different claim from three.
        """
        if not self.measured:
            return "not weighed yet"
        return f"{self.percent}% from {self.sittings} sitting{'' if self.sittings == 1 else 's'}"

    @property
    def evidence(self) -> str:
        """The whole line for this topic's mastery row, caveat included.

        The caveat is on the row rather than in a footnote because a single
        sitting is a guess and the report says so where the student reads the
        number, not somewhere else on the page they may never scroll to.
        """
        if not self.measured:
            return self.summary
        thin = ", so one guess is all this rests on" if self.sittings == 1 else ""
        return f"mastery {self.summary}{thin}"

    @property
    def studied(self) -> str:
        """When this topic was sat, in the student's own days.

        A range rather than one day, because a student who came back to a topic
        a fortnight later has two dates worth knowing and the report that showed
        only the first would be telling them something true and stopping short of
        everything.
        """
        if self.first_on is None:
            return "never sat"
        if self.last_on is None or self.last_on == self.first_on:
            return f"studied {self.first_on:%-d %b %Y}"
        return f"studied {self.first_on:%-d %b %Y} to {self.last_on:%-d %b %Y}"


@dataclass(frozen=True)
class StudyDay:
    """One day the student worked, and what they did on it.

    Sittings, finished topics and skipped topics, because any one alone is a
    partial picture: a student who read a week and marked nothing, or sat a quiz
    on a topic they never put on a week, has studied on a day one column would
    leave out. Skipped is its own column and not folded into finished because a
    skip is a decision the student made and not work they did, which is the same
    distinction the plan page's log draws.

    The line is one sentence written here rather than assembled in the template,
    because a day can have any combination of the three, and seven sentences are
    not something a template should be interleaving correctly forever.
    """

    on: date
    sittings: int
    finished: int
    skipped: int = 0

    @property
    def did(self) -> str:
        """What this day says, or nothing when the record has nothing to say.

        Empty rather than a placeholder: a day with nothing on it is not put in
        the report at all, so a day that reaches here always has one part to say.
        """
        parts = []
        if self.sittings:
            parts.append(f"{self.sittings} sitting{'' if self.sittings == 1 else 's'}")
        if self.finished:
            parts.append(
                f"{self.finished} topic{'' if self.finished == 1 else 's'} finished"
            )
        if self.skipped:
            parts.append(
                f"{self.skipped} topic{'' if self.skipped == 1 else 's'} skipped"
            )
        return ", ".join(parts)


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
    weight_covered: int
    weight_total: int

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
    def enough_topics(self) -> bool:
        """Whether enough of the course's topics have been weighed."""
        return self.covered >= MEASURED_SHARE * self.total

    @property
    def weight_needed(self) -> int:
        """How many units of the course's work have to be weighed for a figure.

        The weight counterpart of ``needed``, so that the two refusals a student
        can hit are both stated in the same shape and the second one does not
        arrive without a number to aim at.
        """
        return ceil(MEASURED_SHARE * self.weight_total)

    @property
    def enough_weight(self) -> bool:
        """Whether enough of the course's weight has been weighed.

        A separate condition from ``enough_topics`` and reported separately,
        because a student who has sat half the topics and almost none of the work
        is refused for a different reason and can only fix it by sitting
        different quizzes.
        """
        return self.weight_covered >= MEASURED_SHARE * self.weight_total

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
        counts = (
            f"You have weighed {self.covered} of this course's {self.total} topics"
        )
        if self.enough_topics:
            counts += (
                f", which is enough of them, but only {self.weight_covered} of this "
                f"course's {self.weight_total} units of work. Readiness needs at "
                f"least {self.weight_needed} of those."
            )
        else:
            counts += (
                f", and readiness needs at least {self.needed} of them before it "
                f"means anything"
            )
        return (
            f"{counts}. A mean over the topics you happened to quiz would be a "
            f"number about your evenings rather than about your course, so this "
            f"report is not printing one. Sit the quizzes on the topics below and "
            f"it will."
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

    @property
    def unweighed_note(self) -> str:
        """The whole sentence about the topics with no sitting, or nothing.

        One sentence in one place, pluralisation and all: a template that spelled
        "that quiz" against "those quizzes" is a second place where the grammar
        of this product is decided, and the two are one edit apart forever.
        """
        if not self.unweighed:
            return ""
        count = len(self.unweighed)
        quiz = "that quiz" if count == 1 else "those quizzes"
        return (
            f"{count} topic{'' if count == 1 else 's'} of your course "
            f"{'has' if count == 1 else 'have'} no sitting at all: "
            f"{self.unweighed_titles}. Sitting {quiz} is what would bring this "
            f"report to a figure."
        )


def report(course: Course) -> Report:
    """The readiness report for this course, read from its own record.

    Every topic of the course, in the order the course teaches them, weighed with
    ``material.mastery`` - the same function the plan page and the quiz page read
    a topic with, so the report cannot print a mastery figure that disagrees with
    the one the student was just looking at.

    The sittings are grouped once by ``mastery.sittings`` and weighed here, rather
    than read again by this module: the report needs the days as well as the
    figures, and two groupings of one table would be two answers to which sitting
    is the latest one.
    """
    topics = list(owned(course.topics.all()))
    taken = mastery.sittings(topics)
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
    figure and the plan are one reading of the same course.

    **The gate is in the same unit as the figure.** It refuses unless the weighed
    topics are both a share of the course's *topics* and a share of its *weight*.
    Counting only topics would let the gate be satisfied by the parts of the
    course that matter least: ten one-unit topics of a twenty topic course sat
    perfectly would clear a topic count of half while holding a fraction of a
    percent of the weight, and the report would print 100% for a course whose
    heaviest topic had never been opened. A gate in one unit with a figure in
    another is how a refusal gets defeated by the data it was refusing.

    Two conditions rather than one because either alone can be gamed: all the
    weight of a course can sit on two topics of twenty, and both topics counted is
    not most of a course; and a course of one-unit topics can be half counted and
    half the weight not counted. The denominator needs no guard: ``Topic.weight``
    is floored at one by the student's own edit and defaults to one, so a course
    with anything weighed on it has a denominator of at least one.
    """
    weighed = [reading for reading in readings if reading.measured]
    held = sum(reading.topic.weight for reading in weighed)
    whole = sum(reading.topic.weight for reading in readings)
    counts = Readiness(
        value=None,
        covered=len(weighed),
        total=len(readings),
        weight_covered=held,
        weight_total=whole,
    )
    if not weighed or not counts.enough_topics or not counts.enough_weight:
        return counts
    value = sum(
        reading.mastery.known * reading.topic.weight for reading in weighed
    ) / held
    return Readiness(
        value=value,
        covered=len(weighed),
        total=len(readings),
        weight_covered=held,
        weight_total=whole,
    )


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
    return tuple(sorted(weighed, key=lambda reading: reading.mastery.known)[:WEAKEST])


def _days(
    readings: Sequence[TopicReading], taken: Mapping[int, list[Attempt]]
) -> tuple[StudyDay, ...]:
    """Every day the record says the student worked, earliest first.

    **Every day ``progress.summarise`` logs, not only the finished ones.** The
    plan page's session log reads ``completed_on or skipped_on``, so a day a
    student chose to skip a topic is a day in their log; this counted only the
    completed ones and would have said the week they skipped something was a week
    they did not study, on the page that reports their whole course. Skipped
    counted apart from finished for the same reason the plan counts them apart: a
    skip is a decision, and a study log that read it as work would be the other
    half of that same lie.

    Plus the sittings, which the plan's log has no row for. So this page can name
    a day the plan page does not - a quiz sat on a topic that was never on this
    week's plan, or sat in a week the plan has since been rebuilt - and that is
    the one direction they may differ in: a day of work with no session on it is
    still a day of work, and the plan page only knows about sessions. The
    direction that would be a contradiction, a day the plan logs and this one
    does not, cannot happen.

    A sitting is dated by its own timestamp in the server's local day rather than
    in UTC, which is the day the student sat it in: ``planning.today`` answers the
    same question for the day a thing is marked, and a study log that disagreed
    with the plan about which day it was would be the sort of pair of answers this
    product keeps refusing to print.
    """
    sittings: dict[date, int] = defaultdict(int)
    finished: dict[date, int] = defaultdict(int)
    skipped: dict[date, int] = defaultdict(int)
    for attempts in taken.values():
        for attempt in attempts:
            sittings[_local(attempt)] += 1
    for reading in readings:
        if reading.topic.completed_on is not None:
            finished[reading.topic.completed_on] += 1
        elif reading.topic.skipped_on is not None:
            skipped[reading.topic.skipped_on] += 1
    return tuple(
        StudyDay(
            on=on, sittings=sittings[on], finished=finished[on], skipped=skipped[on]
        )
        for on in sorted({*sittings, *finished, *skipped})
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