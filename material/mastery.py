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
- **The best sitting is the one mastery is built from, discounted for time.** A
  student who once knew this and has not forgotten still knows it, and a running
  average would spend that knowledge on the sittings they have since muddled
  through. The discount is what makes it an honest reading rather than a flattering
  one: the figure is the best sitting scaled down once per sitting since, which is
  why a student who scored everything right once and has sat it five times since is
  not shown at 100%.
- **Later sittings count for more.** Each sitting is weighted by how many sittings
  came after it, so a lucky early guess is worth less than the same score earned
  later. Without this, one good afternoon on day one would outrank real progress
  made since.

`Attempt.passes` is deliberately not folded in here either. A right answer the cited
paragraph does not word that way is a different signal from a right answer, and
mastery is the figure a student is shown topic by topic; a number that quietly
blended the two would be a third thing that means neither. ``material.readiness``
weighs the mastery this module computes and leaves it alone, and shows how many
sittings each figure rests on so that a student can see whether it is one lucky
sitting before acting on it.

Nothing here is stored. Mastery is a reading of the attempt rows, so it cannot
drift from them: a sitting that is recorded changes the number because the rows
it is read from changed, and there is no second copy to be left behind.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict, Sequence

from material import progress
from material.models import Attempt, Topic
from material.users import owned

#: What one sitting is worth for each sitting that came after it. The most recent
#: is worth its whole score; the one before it is worth this much of it, and the
#: one before that this much squared.
#:
#: Seven tenths because it is the number at which real progress overtakes a lucky
#: guess: a student who scored 75% in their latest sitting outranks the same 75%
#: earned in an earlier one, and one who has fallen to 40% has not lost the topic,
#: they have lost ground on it - which is a different thing and should not read as
#: zero.
#:
#: Counted in sittings and not in days. The rule the product states is that later
#: attempts count for more, and attempt order is the whole of it: five sittings in
#: one evening are not five weeks of forgetting, but they are five pieces of
#: evidence, and weighting them by the calendar would make the figure depend on
#: how often a student opens the quiz page rather than on what they know.
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

    @property
    def measured(self) -> bool:
        """Whether any sitting has been recorded on this topic at all."""
        return self.value is not None

    @property
    def percent(self) -> int:
        """Mastery as the whole number a meter is drawn with.

        Zero rather than an error when nothing has been measured, for the same
        reason the meter is empty there: the page says it is unmeasured, and a bar
        has to be given some width. A weighed topic is never rounded down to that
        zero in practice - the most recent sitting always counts its whole score,
        and a sitting has to be sat to be weighed - so the zero printed here and
        the zero printed for an unmeasured topic cannot be reached by the same
        history.
        """
        return round(self.value * 100) if self.value is not None else 0

    @property
    def known(self) -> float:
        """The value as a plain number, for a caller that has filtered on
        ``measured``.

        Narrowing, not a second reading of the figure: a caller which has already
        dropped the unmeasured topics still holds ``float | None``, and writing
        ``value or 0.0`` at each of those call sites would be the alternative -
        a default repeated once per reader, each of which would have to be right
        about the same filter the reader it sits in just applied.
        """
        return 0.0 if self.value is None else self.value


#: What a topic nobody has sat reads as. Shared rather than rebuilt per topic so
#: that "unmeasured" is one thing the whole application agrees on.
UNMEASURED = Mastery(value=None, sittings=0)


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
        """Whether this topic has been worked through, however well.

        ``completed_on`` and not ``recorded_on``, because a skipped topic and a cut
        topic are out of the outstanding work too and are not one the student
        worked through. Reading the record's whole answer here would print
        "finished" beside a session whose own state says skipped - on the same
        page, two answers to the same question, which is the thing this row exists
        to prevent. How much of a skipped topic they know is mastery's business and
        is a separate column.
        """
        return self.topic.completed_on is not None

    @property
    def state(self) -> str:
        """What the plan calls this topic, said in the word the plan uses.

        The standing column's word, so that this table and the sessions above it
        cannot name one topic two ways.
        """
        if self.complete:
            return Topic.DONE
        if self.topic.skipped_on is not None:
            return Topic.SKIPPED
        if self.topic.cut:
            return Topic.CUT
        return Topic.PLANNED

    @property
    def note(self) -> str:
        """One line saying why there is no number to show.

        Only ever shown where there is no number, so every branch is the absence of
        one. A skipped topic is called a skip rather than "still to do", which it
        is not: the student decided, and that decision is not a gap for them to be
        nagged about.
        """
        if self.complete:
            return (
                "Finished without sitting the quiz, so how much of it you know "
                "has not been weighed. Take the quiz and this becomes a number."
            )
        if self.topic.skipped_on is not None:
            return (
                "Skipped, so nothing has been weighed on it. Skipping costs you "
                "nothing and is not failing at it; take the quiz whenever you "
                "want this to become a number."
            )
        if self.topic.cut:
            return (
                "Off your week, so nothing has been weighed on it. It is still "
                "here, and taking the quiz makes this a number."
            )
        return (
            "Still to do, so nothing has been weighed on it yet. Take the quiz "
            "and this becomes a number."
        )


def for_topic(topic: Topic) -> Mastery:
    """One topic's mastery, from its own sittings.

    Read through ``progress.attempts`` so that the page showing a topic's mastery
    and the rows a sitting wrote cannot answer differently.
    """
    return measure(progress.attempts(topic))


def one(topic: Topic) -> Standing:
    """One topic beside its completion and its mastery.

    The single-topic half of ``standing``, so that the quiz page and the plan page
    build that row in the same place and a change to one is a change to both.
    """
    return Standing(topic=topic, mastery=for_topic(topic))


def sittings(topics: Sequence[Topic]) -> DefaultDict[int, list[Attempt]]:
    """Every sitting taken on these topics, grouped by topic, oldest first.

    The one grouped read of a set of topics' attempt rows, here so that the pages
    which need more than the number - the plan page needs the figures, the
    readiness report needs the days the sittings were taken - are not each
    building their own grouping of the same rows. Two groupings of one table would
    be two answers to "which sitting is the latest", and the day they disagreed
    the report would date a topic's mastery from a different sitting than the
    figure was weighed from.

    A topic nobody has sat is a key that groups to nothing, which is what
    ``measure`` and the report both read as unmeasured.

    Not a plain ``dict``: callers weigh ``grouped[topic.pk]`` without asking
    first, which is the whole point of handing them the grouping rather than a
    per-topic lookup.
    """
    grouped: DefaultDict[int, list[Attempt]] = defaultdict(list)
    for attempt in owned(
        Attempt.objects.filter(topic_id__in=[topic.pk for topic in topics])
    ):
        grouped[attempt.topic_id].append(attempt)
    return grouped


def for_topics(topics: Sequence[Topic]) -> dict[int, Mastery]:
    """Every topic's mastery, from one read of the sittings.

    The plan page shows a row per topic, and a row that asked for its own
    sittings would be one query per topic on the page the student opens most.

    Ordered nowhere on purpose: the order the weighting reads is ``Attempt``'s
    own, the same one ``progress.attempts`` hands ``for_topic``. Naming it here
    too would be a second answer to "which sitting is the latest", and the day the
    two disagreed the quiz page and the plan page would print two different
    mastery figures for one topic.
    """
    grouped = sittings(topics)
    return {topic.pk: measure(grouped[topic.pk]) for topic in topics}


def standing(topics: Sequence[Topic]) -> tuple[Standing, ...]:
    """Every topic beside its completion and its mastery, in one read.

    What the plan page and the quiz page both show, so that a student looking at
    two pages is not given two different pictures of the same topic.
    """
    weighed = for_topics(topics)
    return tuple(
        Standing(topic=topic, mastery=weighed[topic.pk]) for topic in topics
    )


def measure(taken: Sequence[Attempt]) -> Mastery:
    """One topic's mastery from its sittings, oldest first.

    Named rather than kept private because ``material.readiness`` weighs sittings
    it has already read - it needs their dates as well as their scores, and
    weighing them again through ``for_topic`` would be a second read of the same
    rows on the page that reads them once.

    The number is the best sitting's score, discounted once for every sitting
    since. One sitting therefore reads as exactly that score, which is what makes
    "mastery comes from the best scored attempt" true rather than approximate, and
    a later sitting can overtake an earlier one of the same score outright.

    Not clamped, because it cannot leave zero to one: ``progress.record_attempt``
    refuses a score outside that range before it is ever written, and every weight
    this multiplies by is at most one. A guard here would be a claim that
    something is defended which nothing actually tests.
    """
    if not taken:
        return UNMEASURED
    latest = len(taken) - 1
    value = max(
        attempt.score * RECENCY ** (latest - index)
        for index, attempt in enumerate(taken)
    )
    return Mastery(value=value, sittings=len(taken))
