"""Mastery: what the student knows of a topic, which is not the same as having
finished it.

Finishing a topic and knowing a topic came apart in this product on purpose, and
almost every decision here follows from that:

- **Completion is binary and passes at any score.** Sitting a topic's quiz is the
  student saying they have worked through it, and a failing sitting is still
  working through it. Completion is what takes a topic off the outstanding work
  and off the week's projection, so making it depend on a threshold would turn
  the product into something a student abandons at their first hard quiz - and
  would hand them a week built out of topics they have already finished.
- **Mastery is the separate number.** Zero to one, read off the sittings, and
  shown next to completion rather than instead of it.
- **The best sitting is the one mastery is built from.** A student who once knew
  this and has not forgotten still knows it, and a running average would spend
  that knowledge on the sittings they have since muddled through.
- **Later sittings count for more.** Each sitting is weighted by how many sittings
  came after it, so a lucky early guess is worth less than the same score earned
  now. Without this, one good afternoon on day one would outrank real progress
  made six weeks later.

`Attempt.passes` is deliberately not folded in here. A right answer the cited
paragraph does not word that way is a different signal from a right answer, and
weighing the two is what the readiness report is for; a mastery number that
quietly blended them would be a third thing that means neither.

Nothing here is stored. Mastery is a reading of the attempt rows, so it cannot
drift from them: a sitting that is recorded changes the number because the rows
it is read from changed, and there is no second copy to be left behind.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from material import progress
from material.models import Attempt, Topic
from material.users import owned

#: What one sitting is worth for each sitting that came after it. The most recent
#: is worth its whole score; the one before it is worth this much of it, and the
#: one before that this much squared.
#:
#: Seven tenths because it is the number at which real progress overtakes a lucky
#: guess: a student who scored 70% now outranks the same 70% earned six weeks ago,
#: and one who has fallen to 40% has not lost the topic, they have lost ground on
#: it - which is a different thing and should not read as zero.
RECENCY = 0.7


@dataclass(frozen=True)
class Mastery:
    """What the student knows of one topic, and how much sitting says so.

    ``value`` is the number itself, or ``None`` when no sitting has been recorded.
    It is ``None`` rather than zero on purpose: a topic nobody has been quizzed on
    is unmeasured, and printing 0% for it would be a claim - that the student knows
    nothing - which is not what the absence of a sitting says.
    """

    value: float | None
    sittings: int
    best: float | None

    @property
    def measured(self) -> bool:
        """Whether any sitting has been recorded on this topic at all."""
        return self.value is not None

    @property
    def percent(self) -> int:
        """Mastery as the whole number a meter is drawn with.

        Zero rather than an error when nothing has been measured, for the same
        reason the meter is empty there: the page says it is unmeasured, and a bar
        has to be given some width.
        """
        return round(self.value * 100) if self.value is not None else 0


#: What a topic nobody has sat reads as. Shared rather than rebuilt per topic so
#: that "unmeasured" is one thing the whole application agrees on.
UNMEASURED = Mastery(value=None, sittings=0, best=None)


@dataclass(frozen=True)
class Standing:
    """One topic beside where the student has got to with it.

    Completion and mastery in one row on purpose. They are different questions -
    have you finished it, and how much of it do you know - and a page that shows
    only one of them either flatters the student who finished everything or hides
    the topic they finished without understanding.
    """

    topic: Topic
    mastery: Mastery

    @property
    def complete(self) -> bool:
        """Whether this topic is in the student's record, however it got there."""
        return self.topic.recorded_on is not None

    @property
    def note(self) -> str:
        """One line saying which of the two is true, in the order they matter.

        Finished first, because finishing is what took the topic off the week's
        outstanding work, and mastery second because it is what the student still
        has to come back to. A topic that is finished and unweighed says so
        rather than printing a zero, and a topic that is neither says the second
        is because of the first rather than a thing that went wrong.
        """
        if not self.complete:
            return (
                "Still to do, so nothing has been weighed on it yet. Take the "
                "quiz and this becomes a number."
            )
        if not self.mastery.measured:
            return (
                "Finished without sitting the quiz, so how much of it you know "
                "has not been weighed. Take the quiz and this becomes a number."
            )
        return (
            "Finished, and how much of it you know is a separate number from "
            "finishing it."
        )


def for_topic(topic: Topic) -> Mastery:
    """One topic's mastery, from its own sittings.

    Read through ``progress.attempts`` so that the page showing a topic's mastery
    and the rows a sitting wrote cannot answer differently.
    """
    return _measure(progress.attempts(topic))


def for_topics(topics: Sequence[Topic]) -> dict[int, Mastery]:
    """Every topic's mastery, from one read of the sittings.

    The plan page shows a row per topic, and a row that asked for its own
    sittings would be one query per topic on the page the student opens most.
    Ordering is read off ``Attempt.Meta`` rather than sorted here, so the order
    the weighting reads is the order the table promises.
    """
    grouped: dict[int, list[Attempt]] = defaultdict(list)
    for attempt in owned(
        Attempt.objects.filter(topic_id__in=[topic.pk for topic in topics])
    ).order_by("taken_at", "id"):
        grouped[attempt.topic_id].append(attempt)
    return {topic.pk: _measure(grouped[topic.pk]) for topic in topics}


def standing(topics: Sequence[Topic]) -> tuple[Standing, ...]:
    """Every topic beside its completion and its mastery, in one read.

    What the plan page and the quiz page both show, so that a student looking at
    two pages is not given two different pictures of the same topic.
    """
    weighed = for_topics(topics)
    return tuple(
        Standing(topic=topic, mastery=weighed[topic.pk]) for topic in topics
    )


def _measure(taken: Sequence[Attempt]) -> Mastery:
    """One topic's mastery from its sittings, oldest first.

    The number is the best sitting's score, discounted once for every sitting
    since. One sitting therefore reads as exactly that score, which is what makes
    "mastery comes from the best scored attempt" true rather than approximate, and
    a later sitting can overtake an earlier one of the same score outright.

    Clamped, because the weight is arithmetic on a number the grader produced and
    a fraction outside zero to one is not a thing this product is entitled to
    print as a share of what the student knows.
    """
    if not taken:
        return UNMEASURED
    latest = len(taken) - 1
    value = max(
        attempt.score * RECENCY ** (latest - index)
        for index, attempt in enumerate(taken)
    )
    return Mastery(
        value=min(1.0, max(0.0, value)),
        sittings=len(taken),
        best=max(attempt.score for attempt in taken),
    )