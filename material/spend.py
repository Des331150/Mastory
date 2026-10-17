"""What serving one student has cost: written as it is spent, read back later.

Cost and latency are kept per piece of work rather than per model call, because
that is the unit a student would be quoted on and the unit a decision gets made
at: "reading your course in cost four seconds and nothing on anyone's bill;
writing the quizzes you have sat cost two minutes and eleven calls". The calls
inside one piece of work are counted on the row, so nothing has to be aggregated
by whoever reads it.

Three decisions earn their place here.

- **The row is written when the work ends, whether it worked or not.** A
  generation the model refused took the wall clock and gave the student nothing.
  A ledger that only kept what succeeded is a record of the days nothing went
  wrong.
- **It is written outside whatever transaction that work ran in.** The cost of a
  piece of work is not part of the work: a file whose slides were rolled back
  still spent the seconds it spent, and a row inside the transaction would be
  rolled back with them.
- **Nothing is written for looking.** Every page in this product is read as
  often as it is acted on, and a row a visit would make the cost of checking the
  cost a cost of its own. What that rules out is also the promise this product
  makes: no artefact is written before the student reaches it, and one already
  written is not written again.

Money is ``material.models.price`` of what the provider reported its calls
moved, at the one rate the deployment has been told. It is not worked out from
the size of our own requests: an estimate wearing a total's clothes is the one
number on this page nobody would be able to trust.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from material import model
from material.models import Course, Spend, price
from material.users import owned


@contextmanager
def working(*, kind: Spend.Kind, course: Course, label: str = "") -> Iterator[None]:
    """One piece of work the student caused, metered and written down when it ends.

    Wrapped around the work rather than inside it, so that reading a file in,
    working out a topic path, writing a quiz and writing the mock exam are all
    measured the same way. A quiz that is rewritten is a new piece of work and
    a new row: the questions have to be different, and that is model time the
    student asked for, so folding it into the sitting would make a week of
    retaking look like one cheap quiz.

    The seconds are taken around the whole of it rather than around the model
    calls inside, because the slowest piece of work in this product is a PDF
    being converted and no model is involved in that at all.

    ``label`` is what the work was for - a file's name, a topic's title - and it
    is copied onto the row rather than pointed at, because what a week of
    generation cost is worth keeping after the student has renamed the topic or
    taken the file away.
    """
    with model.measuring() as meter:
        started = time.monotonic()
        try:
            yield
        finally:
            Spend.objects.create(
                user_id=course.user_id,
                course=course,
                kind=kind,
                label=label,
                calls=meter.calls,
                seconds=time.monotonic() - started,
                tokens=meter.tokens,
            )


@dataclass(frozen=True)
class Ledger:
    """What one course has cost so far, read off the rows in the order they were
    written.

    Nothing here is stored beside the rows: a total that could fall out of step
    with the work it was totalled from is a second answer to "what did this
    cost", and the day the two disagreed the page would quote a figure for work
    whose rows say something else.

    ``rows`` is what the page lists, so a caller that wants one piece of work
    rather than the course reads the row and not a property of this.
    """

    rows: tuple[Spend, ...]

    @property
    def calls(self) -> int:
        """Every model call this course has caused, which is the figure that
        scales with the size of the material rather than with the length of it."""
        return sum(row.calls for row in self.rows)

    @property
    def seconds(self) -> float:
        """How long the work has taken on this server, which is the half of the
        cost a student waits on: the model calls are the half someone else bills.
        """
        return sum(row.seconds for row in self.rows)

    @property
    def tokens(self) -> int | None:
        """What the whole course has moved, or nothing while a row did not say.

        A total is only as honest as its least known row, so one provider that
        reported nothing takes the total down with it rather than being quietly
        counted as a row of zeros.
        """
        total = 0
        for row in self.rows:
            if row.tokens is None:
                return None
            total += row.tokens
        return total

    @property
    def cost(self) -> float | None:
        """What this course has cost in money, or nothing while that is unknown."""
        return price(self.tokens)

    @property
    def costing(self) -> str:
        """Why there is no money figure here, or nothing because there is one.

        Two absences and both are said out loud, because they are different
        problems and the person reading this is the one who can fix them: a model
        that will not report what it moved is a provider problem, and a rate
        nobody has set is a configuration one.
        """
        if self.tokens is None:
            return (
                "The model did not report what its calls moved, so there is no "
                "cost figure here. What each call moved is what a provider's "
                "reply says, and this one did not say."
            )
        if self.cost is None:
            return (
                "No price is configured for this model, so its tokens are shown "
                "unpriced rather than turned into a money figure nobody has "
                "given us a rate for."
            )
        return ""


def ledger(course: Course) -> Ledger:
    """What this course has cost, in the order the work was done.

    Scoped by the owning user like every other read here, so the page cannot
    become a way to see what another student's course cost.
    """
    return Ledger(rows=tuple(owned(course.spends.all())))