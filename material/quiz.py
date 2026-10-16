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

Taking it again is free, which is what makes the one rule here mean anything. A
student may sit a topic quiz as many times as they like: nothing is spent, no
attempt is charged for, and the only thing a retake changes is what they are
asked. After two failed sittings the third attempt waits until they have been
back to the paragraphs their wrong answers came from, and it says so rather
than refusing silently.

A sitting finishes the topic it was sat on, at any score. That is
``progress.complete``, and it is not the same thing as knowing the topic: how
much of it the student knows is weighed separately in ``material.mastery`` from
the same sittings. The two are kept apart because a threshold on completion
would make the one hard topic of a course the one they can never get past, and
would leave the week full of topics they have already finished.

What an answer is worth is not decided here. Marking lives in
``material.marking``, which the mock exam marks with too: the two kinds of paper
differ in how many attempts they get and what the student is told before they
answer, not in how a sentence is judged.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from django.db import transaction

from material import marking, model, progress, reading
from material.models import PASS_MARK, Question, Quiz, Span, Topic
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

#: How many failed sittings it takes before the student has to go back to the
#: material before another attempt. Two, because the first wrong sitting is
#: information and the second is a habit; blocking on the first would refuse a
#: retry to a student who has only just opened the quiz page.
FAILED_ATTEMPTS_BEFORE_REREAD = 2


@dataclass(frozen=True)
class Reread:
    """What a student still has to open before another attempt is allowed.

    Read rather than returned as a boolean, because the refusal has to name the
    paragraphs or it is not a refusal a student can act on - and because the
    page that shows the block and the rule that raises it must be counting the
    same sittings off the same rows.
    """

    failures: int
    sittings: int = 0
    owed: tuple[Span, ...] = ()

    @property
    def required(self) -> bool:
        """Whether another attempt is being held, as opposed to merely counted."""
        return bool(self.owed)

    @property
    def reason(self) -> str:
        """What the student is told, in one sentence they can act on.

        Says how many times they have sat it and how many of those did not
        stick, rather than only the second number: a student who failed twice,
        passed twice and failed again has sat it five times, and telling them
        they have sat it three is a page arguing with its own database.

        Names what unlocks them and says there is no limit afterwards, because a
        block with no way out of it reads as a punishment and one with a limit
        reads as a quota.
        """
        one = len(self.owed) == 1
        return (
            f"You have sat this quiz {self.sittings} times and not got past "
            f"{PASS_MARK:.0%} in {self.failures} of them, so the next one waits "
            f"until you have been back to "
            f"{'this paragraph' if one else 'these paragraphs'} since then. Open "
            f"{'it' if one else 'them'} and you can sit the quiz again straight "
            f"away, as many times as you like: nothing is spent on a retake."
        )


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


def spent(topic: Topic, quiz: Quiz) -> bool:
    """Whether this quiz has already been sat, and so cannot be sat again.

    A quiz is written once and sat once. Sitting it a second time is a retake of
    the same four questions with the answers still on the page they were just
    marked on, which is the thing regeneration exists to stop - and after a hold
    it is the one route left to a student who would rather not be sent back to
    the material. Refusing it costs nothing: the next attempt is a new quiz, and
    retaking is unlimited.
    """
    taken = progress.attempts(topic)
    return bool(taken) and taken[-1].taken_at > quiz.created_at


def reread(topic: Topic) -> Reread:
    """What this topic's next attempt is waiting for, if anything.

    Free until the student has failed twice. After that it names the paragraphs
    the wrong answers in their last failing sitting came from, less the ones
    they have opened *since that sitting*. The since is the whole rule: reading
    the topic is what this product asks a student to do before sitting the quiz,
    so an all-time record of what they have read would have discharged every hold
    before it applied, and a mechanic that cannot fire is not a mechanic.

    The latest failing sitting is the one that counts rather than every wrong
    answer the topic has ever collected. A student whose last sitting went well
    has already been shown the rest of what they got wrong, and sending them
    back through the whole history is work with nothing at the end of it.

    If the material behind those paragraphs has since been re-uploaded the rows
    are gone with it, and nothing is owed: a hold over a paragraph that no
    longer exists is a hold with no way out of it.
    """
    taken = progress.attempts(topic)
    failures = [attempt for attempt in taken if attempt.failed]
    if len(failures) < FAILED_ATTEMPTS_BEFORE_REREAD:
        return Reread(failures=len(failures), sittings=len(taken))
    holding = failures[-1]
    owed = reading.unopened_since(
        current_user_id(),
        owned(holding.missed.all()),
        since=holding.taken_at,
    )
    return Reread(failures=len(failures), sittings=len(taken), owed=owed)


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


def grade(quiz: Quiz, given: Mapping[str, str], *, on: date) -> marking.Marked:
    """Mark one sitting, record it against the topic, and finish the topic.

    Marking itself is ``material.marking``, shared with the mock exam: what an
    answer is worth does not depend on which kind of paper it was written on.
    This is what happens around it - the sitting is recorded, and the topic is
    finished.

    A short answer counts as right when it contains what the paragraph says - a
    student who writes "the cell membrane is selectively permeable" answered the
    question - while a multiple choice answer counts only when it is the option
    the question was written with.

    A sitting finishes the topic whatever it scored, because finishing is not
    passing. The two are read separately from here on: this writes the day into
    the student's record, and ``material.mastery`` weighs the sitting into a
    number saying how much of the topic they know. A student who got everything
    wrong is a topic off the outstanding work with a low mastery, not a topic
    they are still expected to sit.

    ``on`` is passed in rather than read from the clock, because this module does
    not reach for it: one answer to "what day is it" in one place.

    The sitting and the completion are written together. A sitting recorded
    against a topic that was not finished by it would leave mastery weighed on a
    topic the week still calls outstanding, with nothing on the page saying the
    two ever disagreed.
    """
    result = marking.mark_all(questions(quiz), given)
    if not result.answers:
        raise Rejected(
            "There are no questions on this quiz to mark. Write another one from "
            "a topic with more of your material behind it."
        )
    with transaction.atomic():
        progress.record_attempt(
            quiz.topic,
            score=result.score,
            passes=result.passes,
            # The paragraphs this sitting sent the student back to, kept with the
            # sitting itself: they are what the retry rule reads when it decides
            # whether to hold the next attempt, and a rule that had to guess them
            # from the quiz would be guessing from a quiz that may since have been
            # written again.
            missed=[answer.question.span for answer in result.wrong],
        )
        progress.complete(quiz.topic, on=on)
    return result