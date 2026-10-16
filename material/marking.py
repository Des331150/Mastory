"""Marking one answer, and marking one whole paper.

Both kinds of paper in this product - a topic quiz and the mock exam - are
marked here, and that is the point: the two differ in what they are allowed to
ask of the student (how many attempts, whether questions are written again, what
is said before submitting) and not in how an answer is judged. A rule that graded
the mock exam by a different function from the one grading the topic quiz would
let the same sentence be worth a mark in one and not the other.

Marking is therefore three things, all of them string comparison against the
answers stored with the question:

- **The answers were checked against the paragraph before the question was
  kept.** Every question that reached the student was verified against the span
  it cites, so grading cannot contradict the citation it sends them to.
- **A short answer counts when it says the accepted words, not merely contains
  them.** Containment alone would mark "it is not selective" right on a question
  whose answer is "selective", and would mark "grate" as an answer of "rate": a
  grader that can be argued into a mark is not a check on anything.
- **A right answer the paragraph does not word that way is recorded apart.**
  ``supported`` says the cited paragraph contains what the student wrote, which
  is a different signal from being right, and is what ``material.mastery`` later
  weighs a topic on.

Nothing here knows what a topic, a quiz or an exam is. What a paper *is* belongs
to the modules that hold one; what an answer is worth belongs here.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from material import model
from material.models import Span, is_failure


class Markable(Protocol):
    """One question, as far as marking is concerned.

    What a topic quiz question and a mock exam question have in common, and all
    that marking is allowed to rely on: the kind decides which comparison runs,
    ``answers`` is what counts as right, ``span`` is where the answer has to be
    found for it to count as supported, and ``pk`` is how a hand-in is matched
    to a question. A protocol rather than a shared parent table because the two
    kinds of question have different homes - one lives on a topic and can be
    rewritten, the other lives on an exam that is sat once - and a shared table
    would make that difference a nullable key.
    """

    kind: str
    answers: Sequence[str]
    span: Span
    pk: int

    def __str__(self) -> str: ...


@dataclass(frozen=True)
class Answered:
    """One question, what the student gave, and where a wrong one came from."""

    question: Markable
    given: str
    correct: bool
    supported: bool

    @property
    def citation(self) -> str:
        """Which slide and paragraph this question came from."""
        return self.question.span.citation

    @property
    def anchor(self) -> str:
        """The exact paragraph a wrong answer sends the student to."""
        return self.question.span.anchor


@dataclass(frozen=True)
class Marked:
    """One marked paper: the answers, the share right, and how many verified.

    ``passes`` counts the right answers the cited paragraph itself supports,
    which is kept apart from the score because "knew it" and "got it right for
    some other reason" are different things.

    The score is a fraction of the questions actually asked, and a whole number
    percentage beside it. Both are properties rather than format filters: a
    fraction of one rendered as a percentage is a bar one percent wide for a
    perfect score, and nothing in a test that only reads the heading would
    notice.
    """

    answers: tuple[Answered, ...]
    correct: int
    passes: int

    @property
    def total(self) -> int:
        return len(self.answers)

    @property
    def percent(self) -> int:
        """The share right, as the whole number the meter is drawn with."""
        return round(self.score * 100)

    @property
    def score(self) -> float:
        """The share right, as a fraction of the questions actually asked.

        Zero rather than an error when nothing was asked, so a paper whose every
        question failed verification records nothing rather than recording a
        division by zero.
        """
        return self.correct / self.total if self.total else 0.0

    @property
    def wrong(self) -> tuple[Answered, ...]:
        return tuple(answer for answer in self.answers if not answer.correct)

    @property
    def failed(self) -> bool:
        """Whether this paper went below the pass mark.

        Read off the score through the same function the recorded row uses, so a
        result page cannot call a sitting a pass which the row it just wrote
        calls a failure.
        """
        return is_failure(self.score)


def mark_all(questions: Sequence[Markable], given: Mapping[str, str]) -> Marked:
    """Mark one paper: every question, answered or blank.

    ``given`` is keyed by question id as text. A blank answer is marked wrong
    rather than skipped: a question the student left empty is one they did not
    know, and counting it as neither would let an empty paper score as though it
    had fewer questions in it.
    """
    marked = tuple(
        mark(question, given.get(str(question.pk), "")) for question in questions
    )
    return Marked(
        answers=marked,
        correct=sum(1 for answer in marked if answer.correct),
        passes=sum(1 for answer in marked if answer.correct and answer.supported),
    )


def mark(question: Markable, given: str) -> Answered:
    """One student's answer to one question, marked, and whether the span has it.

    A multiple choice answer counts only when it is the option the question was
    written with; a short answer counts when it says the accepted words on their
    own - whole words, in any order around them - rather than merely containing
    them somewhere. A student who also denies the answer is not credited with it
    either.
    """
    said = model.normalise(given)
    if question.kind == model.MULTIPLE_CHOICE:
        correct = bool(said) and said in {
            model.normalise(choice) for choice in question.answers
        }
    else:
        correct = _short_answer_is_right(said, question.answers)
    return Answered(
        question=question,
        given=given.strip(),
        correct=correct,
        supported=correct and _span_supports(question, said),
    )


#: Ways a student can say no. A denial is a denial of whatever else the answer
#: contains, so an answer carrying one cannot be marking the accepted words.
_DENIAL = frozenset(
    {"not", "no", "never", "none", "neither", "nor", "isnt", "dont", "doesnt"}
)


def _short_answer_is_right(said: str, answers: Sequence[str]) -> bool:
    """Whether the student wrote one of these answers, word for word or around it."""
    if not said or _DENIAL.intersection(said.split()):
        return False
    for accepted in answers:
        wanted = model.normalise(accepted)
        if not wanted:
            continue
        if said == wanted or re.search(rf"\b{re.escape(wanted)}\b", said):
            return True
    return False


def _span_supports(question: Markable, said: str) -> bool:
    """Whether the cited paragraph itself contains the answer the student gave.

    A second signal from the same paper: an answer the paragraph does not
    contain is right by the letter of the question and not by the material, and
    the difference is what mastery is later computed from.
    """
    return bool(said) and said in model.normalise(question.span.text)