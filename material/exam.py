"""The mock exam: one paper across the whole course, sat once, scored as a whole.

It is the second of the two kinds of paper this product has, and it differs from
a topic quiz in the four ways that change what it may ask of a student. Each one
is a decision here rather than a setting on the topic quiz, because a paper that
regenerates is not an exam and a paper that shows its answers while it is being
sat tells the student nothing true about what they know.

- **It covers every topic, not one.** Questions are written topic by topic, in
  the order the course teaches them, so that the paper is the course rather than
  a sample of it. A topic whose paragraphs support no verifiable question is
  named on the page rather than passed over silently: an exam that quietly left
  out a fifth of the syllabus would read as a result.
- **It is written only when the student asks for it.** Nothing here is called
  until they press the button, because a course of twelve topics means twelve
  generation calls and spending them on a page nobody opened is how a product
  bills a student for a sitting they never sat.
- **It is sat once, and the paper never changes.** ``taken_at`` on the exam row
  is the single attempt, and there is no route that writes a second exam or
  regenerates the questions: a retake of the same paper with the answers still
  on the page is a drill, and a student told "you may sit this once" and given a
  second sitting has been lied to about the thing they came for.
- **Nothing is said about their answers until they submit.** The page shows the
  prompts and the options and nothing else - no citation, no mark, no expected
  answer - and every one of those comes back at once when the paper is handed
  in. Naming the paragraph a question came from *before* they answer is reading
  ahead to the answer, so the citation waits until after submission too.

What it is scored on is ``material.marking``, the same function a topic quiz is
marked with. The exam does not record per-topic attempts and does not finish
anything: completion and mastery are read from a topic's own sittings, and one
question per topic sampled once at the end of the course is not evidence about a
topic that four sittings have already said more about.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from django.db import transaction

from material import marking, model, quiz
from material.models import Course, MockExam, MockQuestion, Span, Topic
from material.topics import Rejected
from material.users import current_user_id, owned

#: How many questions a topic contributes to the exam. Two, because an exam that
#: asked one question of each of a dozen topics would measure one fact per topic,
#: and an exam that asked four would be twenty-four model calls before the
#: student had opened a page. What was written and not shown is counted and said.
QUESTIONS_PER_TOPIC = 2

#: What the student is told when they ask for an exam they have already sat, or
#: ask the page that holds their paper for a different one. One wording, because
#: a refusal that says different things on two pages is a page arguing with
#: itself.
ALREADY_SAT = (
    "This is the one attempt your mock exam gets. Your paper and your score are "
    "below, and the questions will not be written again: what is left of the "
    "exam is the material the wrong answers came from."
)


@dataclass(frozen=True)
class Paper:
    """The exam as the student is about to meet it, and as they left it.

    ``covered`` counts the topics the paper actually asked about and ``asked``
    counts the topics of the course, so the difference names the parts of the
    syllabus nothing was written from. That is read off the questions rather than
    stored as a flag on the exam: a stored flag could disagree with the rows it
    describes, and this product has been burned by exactly that before.
    """

    exam: MockExam
    questions: tuple[MockQuestion, ...]
    asked: int

    @property
    def covered(self) -> int:
        """How many of the course's topics this paper asked about."""
        return len({question.topic_id for question in self.questions})

    @property
    def missed_topics(self) -> tuple[str, ...]:
        """The topics nothing on this paper came from, in course order."""
        covered = {question.topic_id for question in self.questions}
        return tuple(
            topic.title
            for topic in _topics(self.exam.course)
            if topic.pk not in covered
        )

    @property
    def whole_course(self) -> bool:
        """Whether every topic of the course is on the paper.

        False is not a failure of the run but a fact about the material, and the
        page says which topics are missing rather than only that the number is
        lower.
        """
        return bool(self.asked) and self.covered >= self.asked

    @property
    def per_topic(self) -> int:
        """How many questions the exam asks about any one topic."""
        return QUESTIONS_PER_TOPIC


def _topics(course: Course) -> list[Topic]:
    """The course's topics, in the order the course teaches them."""
    return list(owned(course.topics.all()))


def coverage(course: Course) -> list[tuple[Topic, list[Span]]]:
    """Every topic of the course with the paragraphs a question could come from.

    Read through the pointer map, topic by topic, rather than as one pool over
    the course: a question can only ever come from a slide the student was told
    some topic covers, and a paper assembled from a whole-course pool would ask
    about paragraphs no topic points at. An empty list for a topic is a topic
    the material cannot support a question from, which is reported rather than
    skipped.
    """
    return [(topic, quiz.topic_spans(topic)) for topic in _topics(course)]


def latest(course: Course) -> MockExam | None:
    """The exam this course has, if the student has asked for one."""
    return MockExam.objects.filter(course=course, user_id=current_user_id()).first()


def find(course: Course, pk: int | None) -> MockExam | None:
    """The exam a form was drawn from, or nothing if that is not this course's.

    A form is marked against the exam it was drawn from rather than against
    whichever exam exists. The course is checked as well as the id, because a
    form post carrying an id from somewhere else must be refused rather than
    graded against this course's paper.
    """
    if pk is None:
        return None
    exam = latest(course)
    return exam if exam is not None and exam.pk == pk else None


def spent(exam: MockExam) -> bool:
    """Whether this exam has been sat, and so cannot be sat again."""
    return exam.sat


def questions(exam: MockExam) -> list[MockQuestion]:
    """The exam's questions, in the order they are asked.

    Read with the paragraph, the slide and the topic behind each one, because the
    marked paper shows the citation on every question and names the topics that
    are not on it, and would otherwise be a query per line.
    """
    return list(
        owned(exam.questions.all()).select_related(
            "span__slide__source_file", "topic"
        )
    )


def paper(exam: MockExam) -> Paper:
    """The exam as a whole: its questions, and the topics it is asked about."""
    return Paper(
        exam=exam,
        questions=tuple(questions(exam)),
        asked=len(_topics(exam.course)),
    )


def build(course: Course) -> MockExam:
    """Write the mock exam for this course, once, from every topic of it.

    Refuses when the course already has one. There is no other route to a second
    exam, and the refusal is the guarantee: a page that asked twice would
    otherwise leave the student holding one attempt of an exam that had been
    quietly replaced, which is the one thing a single attempt is for.

    Every topic is asked about. Questions that cannot be verified against their
    paragraph are dropped rather than shown, exactly as in a topic quiz, and what
    was dropped is counted and shown - so a topic whose material supports nothing
    gets nothing and the page says so by name.
    """
    if latest(course) is not None:
        raise Rejected(ALREADY_SAT)
    covering = coverage(course)
    if not any(spans for _, spans in covering):
        raise Rejected(
            "None of this course's topics point at paragraphs to ask about, so "
            "there is no exam to write. Point the topics at the slides that "
            "cover them first."
        )
    written: list[MockQuestion] = []
    dropped = 0
    with transaction.atomic():
        exam = MockExam.objects.create(user_id=current_user_id(), course=course)
        for topic, spans in covering:
            if not spans:
                continue
            proposals = model.generate_questions(
                topic_title=topic.title,
                spans=[
                    model.SpanExcerpt(index=index, text=span.text)
                    for index, span in enumerate(spans, start=1)
                ],
            )
            kept = proposals.questions[:QUESTIONS_PER_TOPIC]
            # Everything written and not asked counts as left out, including the
            # questions that verified and were dropped only because a topic holds
            # two. A count covering some of the ways a question goes missing would
            # let the page claim nothing was.
            dropped += proposals.dropped + len(proposals.questions) - len(kept)
            by_index = {index: span for index, span in enumerate(spans, start=1)}
            written.extend(
                MockQuestion(
                    user_id=exam.user_id,
                    exam=exam,
                    topic=topic,
                    span=by_index[proposal.span_index],
                    ordinal=len(written) + 1,
                    kind=proposal.kind,
                    prompt=proposal.prompt,
                    choices=list(proposal.choices),
                    answers=list(proposal.answers),
                )
                for proposal in kept
            )
        MockQuestion.objects.bulk_create(written)
        exam.dropped = dropped
        exam.save(update_fields=["dropped"])
    return exam


def given_answers(exam: MockExam) -> dict[str, str]:
    """What the student gave, keyed by question id.

    Read back off the stored rows rather than kept in the response that marked
    them, so that the marked paper a student comes back to is the same marking,
    not a copy of it.
    """
    return {str(question.pk): question.given for question in questions(exam)}


def taken(exam: MockExam) -> marking.Marked | None:
    """The marked exam, re-marked from what was written down, or nothing.

    Nothing rather than an empty result before submission: a paper that has not
    been sat has no marks to show, and showing a score of zero for a sitting
    nobody has taken is feedback of the wrong kind entirely.
    """
    if not spent(exam):
        return None
    return marking.mark_all(questions(exam), given_answers(exam))


def grade(
    exam: MockExam, given: Mapping[str, str], *, on: date
) -> marking.Marked:
    """Mark the one attempt at this exam and record it on the exam itself.

    The answers are written onto the questions with the score beside them, in one
    transaction, so an exam that says it was sat always has the answers it was
    sat with. Marking is refused once the exam is spent: there is no second
    sitting, and a mark taken from a second set of answers would overrule the
    only one the student is allowed.

    Nothing here records a topic attempt or finishes a topic. The exam is one
    sample of the whole course at the end of it, and ``material.mastery`` reads a
    topic from that topic's own sittings; letting an exam's two questions a topic
    outweigh four sittings would make the number describe the exam rather than
    what the student knows.

    ``on`` is passed in rather than read from the clock, because this module does
    not reach for it: one answer to "what day is it" in one place.
    """
    if spent(exam):
        raise Rejected(ALREADY_SAT)
    paper_questions = questions(exam)
    result = marking.mark_all(paper_questions, given)
    if not result.answers:
        raise Rejected(
            "This exam has no questions on it to mark, which is not a sitting. "
            "Ask for a mock exam on a course whose topics point at paragraphs "
            "with something to ask about."
        )
    with transaction.atomic():
        for question in paper_questions:
            question.given = given.get(str(question.pk), "")
            question.save(update_fields=["given"])
        exam.score = result.score
        exam.passes = result.passes
        exam.taken_on = on
        exam.save(update_fields=["score", "passes", "taken_on"])
    return result