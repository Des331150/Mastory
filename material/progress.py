"""How the student is getting on, and the record of what they have done.

Two things live here because they are one thing read two ways. A session the
student marked done or skipped is a line in a log; the same rows counted are the
share of their plan they have done. Splitting them across modules is how a page
ends up showing a log that disagrees with a percentage.

Three decisions earn their place here:

- **The share is of the sessions planned, not of the sessions left.** The
  student typed the number of days and hours; that is what "done a third of my
  plan" has to mean, and measuring against what remains would make finishing
  read as 100% on the last day and 1% on the first.
- **Skipping costs nothing and shows up anyway.** Skipping leaves the numerator
  where it was, so it never takes anything off what the student has done, and
  it leaves the denominator where it was too, so it never flatters them into a
  better share by pretending the session was never planned. It is named on the
  page as a choice, which is the difference between "not doing this" and
  "failing at this".
- **The record lives on the topic, not the session.** A session is the plan's
  row and the plan is rebuilt whenever the hours change; what the student
  finished is theirs and outlives the rebuild.
- **Nothing here accumulates.** There is no streak, no tally and no ranking, and
  the absence is the design rather than an omission: a streak breaks on one
  missed day, which punishes exactly the student the shift-never-compress model
  exists to protect.

Attempts are recorded from the first quiz onwards, because none of it can be
backfilled. The recorder is reached over HTTP by the quiz that marks a sitting,
which calls it once the answers are in; nothing here folds an attempt into a
running total, because mastery is computed from the shape of these rows later.
Each row carries the paragraphs its wrong answers came from, which is the one
thing about an attempt that cannot be worked out later: a quiz written again
leaves nothing behind to work it out from.
"""

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from material.models import Attempt, Session, Span, Topic
from material.topics import Rejected
from material.users import owned


@dataclass(frozen=True)
class LogEntry:
    """One line of the session log: a day, a topic, and what happened."""

    on: date
    position: int
    session: Session
    state: str

    @property
    def topic(self) -> Topic:
        return self.session.topic

    @property
    def minutes(self) -> int:
        return self.session.minutes


@dataclass(frozen=True)
class Summary:
    """What the plan page says about how far the student has got.

    Read from one pass over the sessions rather than counted in the template, so
    that the number on the page, the log under it and the totals the student is
    shown are one answer about one read of the plan.
    """

    planned: int
    completed: int
    skipped: int
    log: tuple[LogEntry, ...]

    @property
    def percent(self) -> int:
        """The share of the planned sessions the student has done.

        Rounded to the nearest whole number, and zero rather than an error when
        there is nothing planned to be a share of.
        """
        if not self.planned:
            return 0
        return round(self.completed * 100 / self.planned)


def summarise(sessions: Sequence[Session]) -> Summary:
    """The share and the log, from the same rows, in one pass.

    Only sessions the student has actually marked appear in the log. A plan is
    mostly sessions nobody has reached yet, and a log made of those is a list of
    things the student has not done yet, which is a backlog wearing a record's
    clothes.
    """
    entries = sorted(
        (
            LogEntry(
                on=session.recorded_on,
                position=session.position,
                session=session,
                state=session.state,
            )
            for session in sessions
            if session.recorded_on is not None
        ),
        key=lambda entry: (entry.on, entry.position),
    )
    return Summary(
        planned=len(sessions),
        completed=sum(1 for entry in entries if entry.state == Topic.DONE),
        skipped=sum(1 for entry in entries if entry.state == Topic.SKIPPED),
        log=tuple(entries),
    )


def mark(session: Session, state: str, *, on: date) -> Session:
    """Put one session's topic into the student's record, or take it back out.

    Marking is a toggle, not a switch: pressing the button for the state a topic
    is already in returns it to untouched. One control per answer means the
    student can correct a mark without a separate "undo" they have to find.

    Done and skipped cannot both be true, so setting one clears the other. The
    log is meant to answer "what happened on the fourth" with a single answer.

    The day is passed in rather than read here, because this module does not
    reach for the clock - one answer to "what day is it" in one place. It is
    written to the topic and not to the session, so that rebuilding the week
    cannot take it away.
    """
    topic = session.topic
    if state == Topic.DONE:
        topic.completed_on = None if topic.completed_on else on
        topic.skipped_on = None
    elif state == Topic.SKIPPED:
        topic.skipped_on = None if topic.skipped_on else on
        topic.completed_on = None
    else:
        raise Rejected("Mark a session done or skipped.")
    topic.save()
    return session


def record_attempt(
    topic: Topic, *, score: float, passes: int, missed: Sequence[Span] = ()
) -> Attempt:
    """One sitting of a topic's quiz, kept as it happened.

    A score outside zero to one is refused rather than stored, because a
    percentage outside that range means the caller is counting marks out of the
    wrong number and every later reading of the table would inherit it. Nothing
    about an attempt is folded into a running total here; mastery will weigh
    these rows later, and it needs them unmixed.

    ``missed`` is the paragraphs the wrong answers in this sitting came from, and
    is part of what the sitting is rather than a note attached to it afterwards:
    it is what the retry rule asks about when it decides whether the next
    attempt waits for the student to go back to their material.
    """
    if not 0.0 <= score <= 1.0:
        raise Rejected("A score is a fraction of the quiz, between nothing and all of it.")
    if passes < 0:
        raise Rejected("A quiz cannot have been verified a negative number of times.")
    attempt = Attempt.objects.create(
        user_id=topic.user_id, topic=topic, score=score, passes=passes
    )
    attempt.missed.add(*missed, through_defaults={"user_id": topic.user_id})
    return attempt


def attempts(topic: Topic) -> list[Attempt]:
    """Everything the student has taken on one topic, oldest first."""
    return list(owned(topic.attempts.all()))