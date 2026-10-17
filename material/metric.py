"""Whether this is helping, read off the record and never asked for.

This is the product's own figure rather than the student's: the share of a
course's topics whose mastery has crossed the line where a student would get
through the exam on them. The spec's standing on it is deliberate - the
student's impression of the real exam is optional confirmation and not the
measurement - and that is also what makes it worth computing at all: a metric
built out of how somebody felt afterwards cannot be read until the exam is over,
cannot be re-derived for any week before that, and answers one question about
one student. Everything below is read from rows this product writes itself, and
none of it can be backfilled.

Two readings, answering two different questions:

- **The metric** is about one course, and its gate is stricter than the
  readiness report's on purpose. Readiness is a mean over what has been sat, and
  a mean over half a course is a fair reading of those topics. This is a
  proportion of the course itself, so it is printed only once every topic has a
  sitting on it: two topics out of twenty crossed would otherwise be reported as
  ten per cent, and a figure about the two evenings a student happened to study
  would be wearing the course's percentage.
- **The correlation** is about students in the plural, because the claim is:
  students who go back to a quiz they failed do better on the mock. Both sides
  are counted and both sides' mock scores are printed; no difference is computed
  from them. A difference worked out from one student on each side is arithmetic
  dressed up as a finding, and the two numbers beside each other are what
  somebody reading this page actually needs.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import DefaultDict

from material import exam as exam_service
from material import readiness
from material.models import Attempt, Course, MockExam, Topic

#: The mastery a topic has to reach for the course to count as got through.
#:
#: Eight in ten rather than the half that counts as passing a single sitting,
#: because the two questions are different. The pass mark says this student got
#: this quiz; the metric says this student would get through the exam on this
#: topic, and a topic they half know is not one of those. Mastery is already
#: discounted once per sitting since, so forgetting is not this line's job twice.
CROSSED = 0.8

#: How the page names that line. Spelled out once here rather than in the
#: template because the sentence describing the figure and the figure itself are
#: two answers to "how much does a topic have to be worth", and the day they
#: described different thresholds the page would be arguing with its own number.
LINE = "four in five"


@dataclass(frozen=True)
class Metric:
    """The share of a course's topics the student has got through, or nothing.

    ``weighed`` is how many of the ``total`` topics have a sitting on them at
    all, and ``unweighed_note`` is ``material.readiness``'s own sentence naming
    the ones that have not - this module does not write a second one, because
    two sentences about which topics are missing is two chances for a page to
    disagree with the report the student read a moment earlier.

    ``readable`` is the whole gate, and it is in the same unit as the figure:
    the figure's denominator is the course's topics, so it is printed only when
    every one of them has been sat. A topic cut off the week still counts in that
    denominator - it is still on the exam, and letting a student raise the figure
    by taking work off it would make the figure a measure of the plan rather than
    of what they know.
    """

    crossed: int
    weighed: int
    total: int
    thinnest: int
    unweighed_note: str

    @property
    def readable(self) -> bool:
        """Whether the whole course has been sat, and so the share is a course's.

        ``total`` false as well as the comparison, because the proportion of no
        topics is not zero of the course: it is the absence of a course to have
        a proportion of.
        """
        return bool(self.total) and self.weighed == self.total

    @property
    def share(self) -> float | None:
        """The share as a fraction of one, or nothing while it cannot be read."""
        return self.crossed / self.total if self.readable else None

    @property
    def percent(self) -> int | None:
        """The share as the whole number the page prints, or nothing where there
        is no share: a bar has to be given some width, but this figure is not a
        bar."""
        share = self.share
        return round(share * 100) if share is not None else None

    @property
    def reason(self) -> str:
        """Why the share is not printed, or nothing because it is.

        Both branches are live: a course with no topics at all is not a course
        with zero of them got through, and a course with three sat of four is
        the case the page exists to refuse.
        """
        if self.readable:
            return ""
        if not self.total:
            return (
                "This course has no topics on it, so there is no share of it to "
                "count. Nothing here can be measured against a topic path."
            )
        return (
            f"You have sat {self.weighed} of this course's {self.total} topics. "
            f"{self.unweighed_note} The share that have got through would be a "
            f"figure about the topics you happened to quiz rather than about "
            f"your course, so it is not printed."
        )

    @property
    def passed(self) -> str:
        """The figure and how much sitting it rests on, in one sentence.

        The thinnest count is in the sentence rather than a footnote because it
        changes what the number means: one sitting is a guess, and the readiness
        report annotates the very same figure for the same reason. A metric that
        read as certain on evidence the report itself calls a guess would be the
        one page in this product that flatters.
        """
        thin = (
            ", and the thinnest of them rests on one sitting, so it is one guess"
            if self.thinnest == 1
            else ""
        )
        one = self.total == 1
        return (
            f"{self.crossed} of this course's {self.total} topic{'' if one else 's'} "
            f"{'is' if one else 'are'} over the {LINE} mastery line{thin}. That "
            f"share is what this product counts itself on."
        )


@dataclass(frozen=True)
class Arm:
    """One side of the comparison: the students on it, and what they sat.

    ``mocks`` is how many mock exams the students on this side have sat between
    them, and ``mean`` is their mean score on those. A mean of nothing is
    ``None`` rather than zero: nobody having sat the mock is not the same claim
    as everybody having scored nothing on it.
    """

    students: int
    mocks: int
    mean: float | None

    @property
    def percent(self) -> int | None:
        """The mean as the whole number the page prints, or nothing where there
        is no mean to print."""
        return round(self.mean * 100) if self.mean is not None else None


@dataclass(frozen=True)
class Side:
    """One side of the claim as the page lists it, so the page does not build the
    pair of them itself and the two rows cannot drift apart."""

    key: str
    name: str
    arm: Arm


@dataclass(frozen=True)
class Correlation:
    """The two sides of the claim this product's mechanic makes.

    ``went_back`` is the students the claim is about: they sat a quiz they had
    failed, which is the thing the retry rule and the citation jump exist to
    produce, and sitting a quiz is what finishes a topic, so their sessions are
    finished work by the same event. ``carried_on`` is everybody else in the
    record.
    """

    went_back: Arm
    carried_on: Arm

    @property
    def sides(self) -> tuple[Side, ...]:
        """The two sides, the one the claim is about first."""
        return (
            Side("went-back", "Went back after failing", self.went_back),
            Side("carried-on", "Never went back", self.carried_on),
        )

    @property
    def comparable(self) -> bool:
        """Whether there is a mock score on both sides to put beside each other.

        Both sides, because a score with nothing to set it against is a score and
        not a comparison. With one student this is false and the page says so; it
        is written here rather than left out because the refusal has to be the
        state that is checked when the second student arrives, and refusing by
        omission is how a page ends up printing a comparison it has not got.
        """
        return bool(self.went_back.mocks and self.carried_on.mocks)

    @property
    def reason(self) -> str:
        """Why there is nothing to compare, or nothing because there is.

        Each sentence names the side that is empty, so the reader can see which
        half of the claim has no evidence under it rather than being told the
        whole thing is early.
        """
        if self.comparable:
            return ""
        if not self.went_back.mocks:
            return (
                "Nobody who has been back to a quiz they failed has sat the mock "
                "yet, so there is no score on that side of the claim to compare."
            )
        return (
            "Nobody who has never been back to a failed quiz has sat the mock "
            "yet, so there is no score on the other side to compare it with."
        )


def metric(course: Course) -> Metric:
    """How much of this course the student has got through.

    Read through ``material.readiness``, which already groups the sittings once
    and names the topics with nothing weighed on them. A second grouping of the
    same rows is the sort of pair of answers this codebase refuses to print, and
    the counts this figure is gated on are that module's.
    """
    report = readiness.report(course)
    return Metric(
        crossed=sum(
            1
            for reading in report.topics
            if reading.measured and reading.mastery.known >= CROSSED
        ),
        weighed=report.readiness.covered,
        total=report.readiness.total,
        thinnest=min(
            (reading.sittings for reading in report.topics if reading.measured),
            default=0,
        ),
        unweighed_note=report.unweighed_note,
    )


def correlation() -> Correlation:
    """Both sides of the claim, counted across every student in the record.

    Deliberately not scoped to the one hardcoded user: the claim is about
    students in the plural, and a reading scoped to one student could never put
    anybody on both sides, which would be a comparison with a half nothing can
    reach. v1 adds real authentication, and this is the one reading that stops
    being the whole record on that day.

    Every number here is derived, and derived from the same rows the rest of the
    product reads: a sitting from ``Attempt``, and a mock score re-marked by
    ``material.exam`` rather than read off a column nothing writes.
    """
    scores = _mock_scores()
    went_back = _went_back()
    return Correlation(
        went_back=_arm(went_back, scores),
        carried_on=_arm(_students() - went_back, scores),
    )


def _students() -> set[int]:
    """Every student this record holds anything for.

    Sittings, finished topics and sat mocks, unioned: a student who marked their
    sessions done and never sat a quiz is a student this product has a record
    of, and leaving them out would put them on nobody's side of a comparison
    about what students did.
    """
    return (
        set(Attempt.objects.values_list("user_id", flat=True))
        | set(
            Topic.objects.filter(
                completed_on__isnull=False
            ).values_list("user_id", flat=True)
        )
        | set(
            MockExam.objects.filter(taken_on__isnull=False).values_list(
                "user_id", flat=True
            )
        )
    )


def _went_back() -> set[int]:
    """The students who sat a quiz they had already failed.

    Read off the sittings rather than off a flag, because the product keeps no
    flag saying a student went back and should not: a mechanic that records
    itself is a mechanic that can disagree with what it did. A sitting after a
    failing one on the same topic is the event itself.

    Grouped here rather than through ``material.mastery.sittings`` because that
    is the one grouped read of a *student's* sittings, and this is a question
    about which student did something across every student in the record. The
    rows arrive in the order ``Attempt`` declares, which is the order everything
    else that weighs sittings reads them in; two sittings can share a timestamp
    to the microsecond, so a later sitting is found by walking them rather than
    by asking the database which one was later.
    """
    by_topic: DefaultDict[tuple[int, int], list[Attempt]] = defaultdict(list)
    for attempt in Attempt.objects.all():
        by_topic[(attempt.user_id, attempt.topic_id)].append(attempt)
    return {
        user for (user, _), taken in by_topic.items() if _went_back_on(taken)
    }


def _went_back_on(taken: Sequence[Attempt]) -> bool:
    """Whether one of these sittings came after a failing one on the same topic.

    Any earlier failure counts rather than only the most recent one, because the
    claim is about going back at all.
    """
    failed = False
    for attempt in taken:
        if failed:
            return True
        failed = attempt.failed
    return False


def _arm(students: set[int], scores: dict[int, list[float]]) -> Arm:
    """One side of the claim: how many students are on it and what they scored.

    The mean is over the mock exams those students sat rather than over the
    students, so a student who sat two mocks counts twice: they are two pieces
    of evidence about the mock, and averaging per student first would weight a
    student who sat one paper the same as one who sat four.

    The scores are read once for both arms rather than once per arm, because
    re-marking a paper is the expensive half of this reading and a total the two
    sides disagree about would be a figure from two different questions.
    """
    marks = [score for student in students for score in scores.get(student, [])]
    return Arm(
        students=len(students),
        mocks=len(marks),
        mean=sum(marks) / len(marks) if marks else None,
    )


def _mock_scores() -> dict[int, list[float]]:
    """What each student scored on every mock they sat, marked as they sat it.

    Re-marked rather than read off a column, because the exam stores the answers
    and not the score, and a cohort mean made by any other function would be a
    second verdict on a paper.
    """
    scores: DefaultDict[int, list[float]] = defaultdict(list)
    for exam in MockExam.objects.filter(taken_on__isnull=False):
        scores[exam.user_id].append(exam_service.score_of(exam))
    return dict(scores)