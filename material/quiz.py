"""A topic's quiz: questions written from the student's own paragraphs, and
what happens when they answer one wrong.

Three decisions earn their place here.

- **The span comes first.** A question is written from one paragraph of this
  student's material, cites that paragraph, and is checked against it before it
  is kept. The model boundary owns that check; this module asks it for questions
  and takes what survives. Nothing here invents a question, repairs one, or asks
  the model to fill a gap in a quiz that came out short.
- **A short quiz is the honest outcome, and it says so.** Questions that cannot
  be verified are dropped rather than shown, so a topic whose material supports
  one question gets one question. That is flagged low-confidence and the student
  is told how many questions were left out, because a quiz that silently got
  smaller reads as a fault rather than as a decision.
- **Grading cannot contradict the citation.** Every accepted answer was verified
  against the span before the question was stored, and marking is a comparison
  against those stored answers. A student is sent to the exact paragraph their
  answer came from when they get it wrong, which is the point of the citation:
  not a number, but the paragraph to re-read.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from django.db import transaction

from material import model, progress
from material.models import Question, Quiz, Span, Topic
from material.topics import Rejected
from material.users import current_user_id, owned

#: How many questions a topic's quiz asks for. The product states this rather
#: than hiding it, because a student told "a short quiz" and given nine has been
#: lied to about the size of the thing they are about to sit.
QUESTION_LIMIT = 4

#: Below this many verified questions the quiz is flagged low-confidence. One
#: question measures almost nothing, and a score shown without that caveat
#: invites a conclusion the material cannot support.
SHORT_QUIZ_UNDER = 3


@dataclass(frozen=True)
class Answered:
    """One question, what the student gave, and where a wrong one came from."""

    question: Question
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
        return self.question.anchor


@dataclass(frozen=True)
class Result:
    """A graded sitting: the answers, the share right, and the quiz it was on.

    ``passes`` counts the right answers the cited paragraph itself supports,
    which is kept apart from the score because "knew it" and "got it right for
    some other reason" are different things and mastery is later computed from
    the difference.
    """

    quiz: Quiz
    answers: tuple[Answered, ...]
    correct: int
    passes: int

    @property
    def total(self) -> int:
        return len(self.answers)

    @property
    def percent(self) -> int:
        """The share right, as the whole number the meter is drawn with.

        A property rather than a format filter: a fraction of one rendered as a
        percentage is a bar one percent wide for a perfect score, and nothing in
        a test that only reads the heading would notice.
        """
        return round(self.score * 100)

    @property
    def score(self) -> float:
        """The share right, as a fraction of the questions actually asked.

        Zero rather than an error when nothing was asked, so a quiz whose every
        question failed verification records nothing rather than recording a
        division by zero.
        """
        return self.correct / self.total if self.total else 0.0

    @property
    def wrong(self) -> tuple[Answered, ...]:
        return tuple(answer for answer in self.answers if not answer.correct)


def topic_spans(topic: Topic) -> list[Span]:
    """The paragraphs of every slide this topic points at, in course order.

    The question pool, read through the pointer map rather than through the
    course, so a question can only ever come from a slide the student was told
    this topic covers. A paragraph with no words in it - a bare picture - is
    left out: there is nothing to ask about and nothing to verify an answer
    against.
    """
    return [
        span
        for slide in topic.slides
        for span in owned(slide.spans.all()).order_by("ordinal")
        if span.text.strip()
    ]


def latest(topic: Topic) -> Quiz | None:
    """The quiz this topic currently has, if it has one."""
    return owned(topic.quizzes.all()).first()


def find(topic: Topic, pk: int) -> Quiz | None:
    """One of this topic's own quizzes, or nothing if that is not one of them.

    A form is marked against the quiz it was drawn from rather than against
    whichever quiz happens to be newest. A student who opens a second tab and
    asks for different questions, then submits the page they were reading, has
    answered questions that no longer exist; grading that form against the new
    quiz would read every answer as blank and record a zero they did not earn.
    """
    return owned(topic.quizzes.all()).filter(pk=pk).first()


def build(topic: Topic) -> Quiz:
    """A fresh quiz for this topic, written from its own paragraphs.

    Replaces whatever was there, which is what taking it again means: the
    questions are written again, so an answer worked out last time is not an
    answer memorised this time. Questions that cannot be verified are dropped,
    and the count of them is kept so the page can say what it left out.
    """
    spans = topic_spans(topic)
    if not spans:
        raise Rejected(
            "This topic points at no paragraphs to ask about, so there is no quiz "
            "to write. Point it at the slides that cover it first."
        )
    written = model.generate_questions(
        topic_title=topic.title,
        spans=[
            model.SpanExcerpt(index=index, text=span.text)
            for index, span in enumerate(spans, start=1)
        ],
    )
    kept = written.questions[:QUESTION_LIMIT]
    by_index = {index: span for index, span in enumerate(spans, start=1)}
    with transaction.atomic():
        owned(topic.quizzes.all()).delete()
        quiz = Quiz.objects.create(
            user_id=current_user_id(),
            topic=topic,
            # Everything written and not asked counts as left out, including the
            # questions that verified and were dropped only because a short quiz
            # holds four. A count that covered some of the ways a question goes
            # missing would let the page claim nothing was.
            dropped=written.dropped + len(written.questions) - len(kept),
            low_confidence=len(kept) < SHORT_QUIZ_UNDER,
        )
        Question.objects.bulk_create(
            [
                Question(
                    user_id=quiz.user_id,
                    quiz=quiz,
                    span=by_index[proposal.span_index],
                    ordinal=ordinal,
                    kind=proposal.kind,
                    prompt=proposal.prompt,
                    choices=list(proposal.choices),
                    answers=list(proposal.answers),
                )
                for ordinal, proposal in enumerate(kept)
            ]
        )
    return quiz


def questions(quiz: Quiz) -> list[Question]:
    """The quiz's questions, in the order they are asked.

    Read with the paragraph and the slide behind each one, because a marked
    sitting shows the citation on every question and would otherwise be a query
    per line.
    """
    return list(owned(quiz.questions.all()).select_related("span__slide__source_file"))


def grade(quiz: Quiz, given: Mapping[str, str]) -> Result:
    """Mark one sitting and record it against the topic.

    Marking is string comparison against the answers stored with the question,
    which were checked against the cited paragraph when the question was made.
    A short answer also counts as right when it contains what the paragraph
    says - a student who writes "the cell membrane is selectively permeable"
    answered the question - while a multiple choice answer counts only when it
    is the option the question was written with.
    """
    marked = tuple(
        _answer(question, given.get(str(question.pk), ""))
        for question in questions(quiz)
    )
    if not marked:
        raise Rejected(
            "There are no questions on this quiz to mark. Write another one from "
            "a topic with more of your material behind it."
        )
    result = Result(
        quiz=quiz,
        answers=marked,
        correct=sum(1 for answer in marked if answer.correct),
        passes=sum(1 for answer in marked if answer.correct and answer.supported),
    )
    progress.record_attempt(quiz.topic, score=result.score, passes=result.passes)
    return result


def _answer(question: Question, given: str) -> Answered:
    """One student's answer, marked, and whether the span supports it.

    A short answer counts as right when it says the accepted words on their own
    - whole words, in any order around them - rather than merely containing
    them somewhere. Containment alone would mark "it is not selective" right on a
    question whose answer is "selective", and would mark "grate" as an answer of
    "rate": a grader that can be argued into a mark is not a check on anything.
    A student who also denies the answer is not credited with it either.
    """
    said = model.normalise(given)
    if question.kind == Question.Kind.MULTIPLE_CHOICE:
        correct = bool(said) and said in {
            model.normalise(choice) for choice in question.answers
        }
    else:
        correct = _short_answer_is_right(said, question.answers)
    return Answered(
        question=question,
        given=given.strip(),
        correct=correct,
        supported=correct and _span_supports(question, given),
    )


#: Ways a student can say no. A denial is a denial of whatever else the answer
#: contains, so an answer carrying one cannot be marking the accepted words.
_DENIAL = frozenset({"not", "no", "never", "none", "neither", "nor", "isnt", "dont", "doesnt"})


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


def _span_supports(question: Question, given: str) -> bool:
    """Whether the cited paragraph itself contains the answer the student gave.

    A second signal from the same sitting: an answer the paragraph does not
    contain is right by the letter of the question and not by the material, and
    the difference is what mastery is later computed from.
    """
    said = model.normalise(given)
    return bool(said) and said in model.normalise(question.span.text)