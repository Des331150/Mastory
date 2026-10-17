"""Shared helpers for the HTTP-seam tests.

Every test drives the real Django application through the test client, so the
helpers here stay close to what a student does: POST a PDF, read the response.

The one thing a test cannot do for real is talk to a model. ``use_fake_model``
installs a deterministic answer at ``material.model.complete``, the single
function Mastory leaves the machine through, so every test above the fake is the
real application.
"""

import json
import re
from contextlib import contextmanager
from datetime import date, datetime, time
from fractions import Fraction
from html import unescape
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.timezone import make_aware

from material import model, planning
from material.models import MockExam

#: One topic as the fake model claims it: a title, the slides it cites by their
#: course position, and how sure it is.
Claim = dict[str, Any]

FIXTURES = Path(__file__).resolve().parent / "fixtures"

LINEAR_ALGEBRA_PDF = FIXTURES / "math-201-eigenvalues.pdf"
CELL_BIOLOGY_PDF = FIXTURES / "bio-110-membrane-transport.pdf"
HANDOUT_NOTES_PDF = FIXTURES / "che-212-handout-notes.pdf"
HANDOUT_SCAN_PDF = FIXTURES / "che-212-handout-scan.pdf"
BLANK_SCAN_PDF = FIXTURES / "che-212-blank-scan.pdf"
LECTURE_DECK_PDF = FIXTURES / "che-212-lecture-deck.pdf"


def pdf_upload(
    path: Path = LINEAR_ALGEBRA_PDF, name: str | None = None
) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name or path.name, path.read_bytes(), content_type="application/pdf"
    )


def pdf_uploads(*paths: Path) -> list[SimpleUploadedFile]:
    """Several files chosen together, as a multi-file upload sends them."""
    return [pdf_upload(path) for path in paths]


def ocr_is_available() -> bool:
    """Whether this machine can read text out of a picture.

    Reading a scanned page needs Tesseract's data files, which are not part of
    the project dependencies. A test that needs the words back says so rather
    than passing on an empty page.
    """
    import pymupdf

    try:
        pymupdf.get_tessdata()  # type: ignore[no-untyped-call]
    except Exception:
        return False
    return True


def broken_pdf_upload(name: str = "corrupt-deck.pdf") -> SimpleUploadedFile:
    """A file that claims to be a PDF and is not one."""
    return SimpleUploadedFile(
        name, b"%PDF-1.7\nthis is not really a pdf", content_type="application/pdf"
    )


def not_a_pdf_upload(name: str = "notes.txt") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"just some notes", content_type="text/plain")


@contextmanager
def temporary_media_root() -> Iterator[Path]:
    """Point MEDIA_ROOT at a scratch directory so tests leave nothing behind."""
    with TemporaryDirectory() as directory:
        with override_settings(MEDIA_ROOT=Path(directory)):
            yield Path(directory)


def use_temporary_media_root(test_case: TestCase) -> None:
    """Give a TestCase a scratch MEDIA_ROOT for the duration of each test."""
    manager = temporary_media_root()
    manager.__enter__()
    test_case.addCleanup(manager.__exit__, None, None, None)


def fake_topic(title: str, slides: list[int], confidence: float = 0.9) -> Claim:
    """One topic the fake model claims, citing slides by their course position."""
    return {"title": title, "slides": slides, "confidence": confidence}


def flat(text: str) -> str:
    """The page as a student reads it: no line wrapping, no entities.

    Entities are decoded because the copy says "this course's" and the page a
    test reads it off says ``this course&#x27;s``; a test that had to know about
    the escaping would be testing the template engine rather than the product.
    """
    return " ".join(unescape(text).split())


#: A Monday, so a day a thing happened on can be read back off the page. A Monday
#: rather than an arbitrary date because the plan puts sessions on study days and
#: a test that pinned a Sunday would be reading a week that never had one.
A_MONDAY = date(2026, 11, 2)


@contextmanager
def on_a_monday(moment: date = A_MONDAY) -> Iterator[None]:
    """Do the student's work as though they were living on a known day.

    Patches both clocks a sitting is stamped with, because a sitting is stamped
    twice: ``planning.today`` dates the topic being finished, and the model's
    ``auto_now_add`` dates the moment the quiz was handed in. A test that pinned
    one and not the other would put a sitting on one day and the work it finished
    on another, and any assertion about days would be reading a day that no
    student ever lived.
    """
    with (
        mock.patch.object(planning, "today", return_value=moment),
        mock.patch(
            "django.db.models.fields.timezone.now", return_value=_at(moment)
        ),
    ):
        yield


def _at(day: date) -> datetime:
    """Midday on that day, as an aware moment, for the row a sitting writes.

    Midday rather than midnight so that a timestamp near a day's edge cannot be
    read as the day before or after it in whatever timezone the row is rendered
    in.
    """
    return make_aware(
        datetime.combine(day, time(hour=12)), timezone.get_default_timezone()
    )



class QuizSittings:
    """Sitting a topic's quiz through the browser, for any HTTP test that needs it.

    The one driver for "sit this topic and mark it this way", shared rather than
    written again per test module: the mastery tests, the retry tests and the
    readiness report all sit quizzes, and three copies of the form-posting would
    be three places for a change to the quiz page to be forgotten.

    Takes a ``TestCase`` as a base class, not a fixture, because sitting a quiz
    needs the client's assertions and a fake model installed per sitting.
    """

    def topic_id(self, title: str, course_id: int = 1) -> str:
        """The topic's id, read off the topic page the way a student finds it."""
        response = self.client.get(f"/courses/{course_id}/topics/")  # type: ignore[attr-defined]
        return topic_pk(response.content.decode(), title)

    def quiz_url(self, title: str, course_id: int = 1) -> str:
        """Where this topic's quiz is, for a title the student recognises."""
        return f"/courses/{course_id}/topics/{self.topic_id(title, course_id)}/quiz/"

    def quiz_page(self, title: str, course_id: int = 1) -> str:
        """The quiz page for one topic, as the student opens it."""
        response = self.client.get(self.quiz_url(title, course_id))  # type: ignore[attr-defined]
        assert response.status_code == 200, f"the quiz for {title!r} did not open"
        return flat(response.content.decode())

    def sit(
        self,
        title: str,
        right: Fraction = Fraction(1),
        *,
        course_id: int = 1,
        moment: date | None = None,
        question: dict[str, Any] | None = None,
    ) -> Fraction:
        """Sit this topic's quiz once, marking ``right`` of the questions correct.

        ``right`` is a share of however many questions survived verification, and
        the score that sitting recorded is returned as the exact fraction of them
        rather than as a float: a test computing an expected mean from it is then
        doing arithmetic instead of rounding twice.

        The url and the quiz id are read off the page rather than built, so the
        sitting is marked on the quiz the student was actually looking at, as a
        browser would send it.
        """
        source = question or DEFAULT_QUESTION
        QuizModel(source).use(self)  # type: ignore[arg-type]
        day = moment or A_MONDAY
        with on_a_monday(day):
            html = self.quiz_page(title, course_id)
            questions = re.findall(r'id="question-(\d+)"', html)
            assert questions, f"there are no questions on {title!r}'s quiz to answer"
            correct = round(right * len(questions))
            answers = {}
            for index, question_id in enumerate(questions):
                choices = re.findall(
                    r'value="([^"]*)"',
                    _question_block(html, f'id="question-{question_id}"'),
                )
                assert len(choices) >= 2, (
                    f"question {question_id} offers nothing to get wrong"
                )
                answers[question_id] = choices[0 if index < correct else -1]
            action = re.search(r'action="([^"]+)"', html)
            which = re.search(r'name="quiz" value="(\d+)"', html)
            assert action is not None and which is not None, "no form to hand in"
            response = self.client.post(  # type: ignore[attr-defined]
                action.group(1),
                {
                    "quiz": which.group(1),
                    **{f"q-{key}": value for key, value in answers.items()},
                },
                follow=True,
            )
            assert response.status_code == 200, "the sitting was not accepted"
            # A refused sitting re-renders the quiz page with the refusal on it,
            # and still answers 200, so the status code says nothing here. Without
            # this a held retake or a re-marked quiz would hand back a score for a
            # sitting that was never recorded, and every later assertion would be
            # quietly reading the previous one.
            assert 'class="error"' not in response.content.decode(), (
                f"the sitting on {title!r} was refused"
            )
        return Fraction(correct, len(questions))

    def sit_again(
        self,
        title: str,
        right: Fraction = Fraction(1),
        *,
        course_id: int = 1,
        moment: date | None = None,
        question: dict[str, Any] | None = None,
    ) -> Fraction:
        """Take a topic's quiz a second time, on a new set of questions.

        A retake has to be a new quiz: marking the same questions twice is the
        thing regeneration exists to stop, and a test asking for a second sitting
        that silently re-marks the first would be asserting nothing about it.
        """
        source = question or DEFAULT_QUESTION
        QuizModel(source).use(self)  # type: ignore[arg-type]
        day = moment or A_MONDAY
        with on_a_monday(day):
            response = self.client.post(  # type: ignore[attr-defined]
                f"{self.quiz_url(title, course_id)}rewrite/", follow=True
            )
            assert response.status_code == 200
            assert re.findall(
                r'id="question-\d+"', response.content.decode()
            ), f"the rewrite of {title!r} produced no questions to sit"
            assert 'class="error"' not in response.content.decode(), (
                f"the rewrite of {title!r} was refused"
            )
        return self.sit(title, right, course_id=course_id, moment=day, question=source)


def _question_block(html: str, opening: str) -> str:
    """The question carrying ``opening``, cut out of a quiz page.

    Cut at the next question rather than at the next list item, because a
    question's choices are list items of their own and a shorter cut would leave
    one option to answer with.
    """
    for chunk in html.split('<li class="question')[1:]:
        if opening in chunk:
            return chunk.split('<li class="question')[0]
    raise AssertionError(f"the page has no question with {opening}")


def topic_pk(html: str, title: str) -> str:
    """The id a topic carries on the topic page, found the way a student reads it.

    Inferred paths are replaced wholesale, so a row's id is not stable across
    requests and a test must not assume one; this reads it off the page instead.
    """
    for block in html.split('<li class="topic')[1:]:
        if f". {title}</h2>" in block:
            return re.search(r'id="topic-(\d+)"', block).group(1)  # type: ignore[union-attr]
    raise AssertionError(f"no topic titled {title!r} on the topic path")


def panel(html: str, name: str) -> str:
    """One panel of a page, cut out of it by the class it carries.

    A page carrying several panels is one a whole-page search would answer from
    the wrong one, and a test that asserts against whichever panel happened to
    come first is a test that stops being about its subject the day the order
    changes.
    """
    found = html.split(f'<section class="panel {name}')[1].split("</section>")[0]
    return found


def attribute(html: str, name: str) -> str:
    """One attribute the way the page printed it, as text."""
    found = re.search(rf'{name}="([^"]*)"', html)
    assert found is not None, f"the page carries no {name}: {html}"
    return str(found.group(1))


def report_call(prompt: str, reply: str, *, silent: bool = False) -> None:
    """Tell the cost meter about this call, the way a provider's reply does.

    A real endpoint sends a ``usage`` block with every reply and
    ``material.model.complete`` reads it, so a fake that reported nothing would
    leave every cost in the suite at zero and the priced figure unexercised - the
    one promise this seam cannot check would be the one thing no test could see.
    Counting characters as tokens is close enough: the number belongs to the
    fake, and the price is whatever the test sets.

    ``silent`` answers without saying what the call moved, which is what a
    provider whose replies carry no counts looks like, and the cost page has to
    cope with that rather than pricing the course at nothing.
    """
    model.note_call(None if silent else (len(prompt) + len(reply)) // 4)


def use_fake_model(
    *topics: Claim, reply: str | None = None, silent: bool = False
) -> Any:
    """Answer every model call in this test with these topics.

    Returns the patcher so it can be used as a context manager or started with
    ``addCleanup``. The application above the fake is entirely real: the reply
    still has to be grounded in the material, because that happens in
    ``material.model`` rather than here.

    ``silent`` answers without saying what the call moved.
    """

    answer = reply if reply is not None else json.dumps({"topics": list(topics)})

    def says(prompt: str) -> str:
        report_call(prompt, answer, silent=silent)
        return answer

    patcher = mock.patch.object(model, "complete", side_effect=says, name="complete")
    return patcher


def fake_question(
    span: int,
    prompt: str,
    *,
    kind: str = "multiple_choice",
    choices: list[str] | None = None,
    answer: str = "",
    answers: list[str] | None = None,
) -> dict[str, Any]:
    """One question the fake model writes from the span it cites.

    ``span`` is the span's position in the paragraphs the application handed
    over, which is what a real reply cites; the application is responsible for
    turning it back into the paragraph, and a test that cites a span that was
    never supplied is testing that it does not.
    """
    claim: dict[str, Any] = {"span": span, "kind": kind, "prompt": prompt}
    if kind == "multiple_choice":
        claim["choices"] = choices or [answer, "None of these"]
        claim["answer"] = answer
    else:
        claim["answers"] = answers or [answer]
    return claim


class QuizModel:
    """A fake model that answers a quiz the way a model would.

    It reads the request rather than replaying a canned string, because a quiz
    takes two kinds of call: one asking for questions from the paragraphs, and
    one per question asking whether the paragraph supports the answer. The fake
    parses the JSON it is handed, quotes the paragraphs it was given, and
    supports an answer only when that answer really is in the paragraph it was
    written from - which is what lets a test drive the verification step
    through the seam rather than stubbing it out.
    """

    def __init__(
        self,
        *questions: dict[str, Any],
        verified: tuple[int, ...] | None = None,
    ) -> None:
        self.prompts: list[str] = []
        self.verified = None if verified is None else set(verified)
        self._questions = list(questions)

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        request = json.loads(prompt)
        if "supported" in request["instruction"]:
            reply = json.dumps({"supported": self._verify(request)})
        else:
            reply = json.dumps({"questions": self._write(request)})
        report_call(prompt, reply)
        return reply

    def _write(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """One question per span the fake was asked about, citing that span."""
        written = []
        for position, span in enumerate(request["spans"], start=1):
            source = self._questions[(position - 1) % len(self._questions)]
            claim = dict(source)
            claim["span"] = span["span"]
            if claim.get("answers"):
                claim["answers"] = self._first_in(span["text"], claim["answers"])
            if claim.get("choices"):
                claim["choices"], claim["answer"] = self._choice_in(
                    span["text"], claim["choices"], claim.get("answer", "")
                )
            written.append(claim)
        return written

    def _choice_in(
        self, text: str, choices: list[str], answer: str
    ) -> tuple[list[str], str]:
        """The right option the paragraph actually contains, and its distractors.

        Drawn from the paragraph's own sentences so that the options a student
        is offered are words that appear in the material they are being asked
        about, which is the case a real model has to get right.
        """
        sentences = [s.strip(" .") for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        words = sentences or [text.strip(" .")]
        right = next((w for w in words if answer and answer in w), None)
        if right is None:
            right = words[0]
        distractors = [w for w in words if w != right][: max(0, len(choices) - 1)]
        while len(distractors) < len(choices) - 1:
            distractors.append(f"Not stated in the paragraph {len(distractors) + 1}")
        return [right, *distractors[: len(choices) - 1]], right

    def _first_in(self, text: str, answers: list[str]) -> list[str]:
        """The first offered answer the paragraph contains, or the first one.

        A short answer the paragraph never uses is left as the model wrote it,
        so the application's own grounding check is what refuses it.
        """
        return [next((a for a in answers if a in text), answers[0])]

    def _verify(self, request: dict[str, Any]) -> bool:
        """Whether the paragraph really states the answer, unless told otherwise.

        ``verified`` names the spans this fake will agree about; a test sets it
        to make the model vouch for a paragraph that does not say the answer,
        which is the only way to see the drop from the student's side.
        """
        number = request["span"]["span"]
        if self.verified is not None and number not in self.verified:
            return False
        return all(a in request["span"]["text"] for a in request["question"]["answers"])

    @property
    def questions_written(self) -> list[dict[str, Any]]:
        """The generation request, as the fake model read it."""
        for prompt in self.prompts:
            request = json.loads(prompt)
            if "questions" in request["instruction"]:
                spans: list[dict[str, Any]] = request["spans"]
                return spans
        raise AssertionError("the model was never asked for questions")

    @property
    def written_for_paragraphs(self) -> int:
        """How many questions this fake actually wrote, spans and all."""
        return sum(
            len(self._write(json.loads(prompt)))
            for prompt in self.prompts
            if "questions" in json.loads(prompt)["instruction"]
        )

    def use(self, test_case: TestCase) -> None:
        patcher = mock.patch.object(model, "complete", new=self)
        patcher.start()
        test_case.addCleanup(patcher.stop)


class RecordingModel:
    """A fake model that remembers what it was asked.

    Reads the request the way a model does, so a test can assert that the
    material reaching the model is the student's own and shaped the way the
    contract says, without reaching past the HTTP seam to look at it.
    """

    def __init__(self, *topics: Claim, reply: str | None = None) -> None:
        self.prompts: list[str] = []
        self._answer = reply if reply is not None else json.dumps(
            {"topics": list(topics)}
        )

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        report_call(prompt, self._answer)
        return self._answer

    @property
    def last_request(self) -> dict[str, Any]:
        assert self.prompts, "the model was never called"
        request: dict[str, Any] = json.loads(self.prompts[-1])
        return request

    def use(self, test_case: TestCase) -> None:
        """Answer every model call for the duration of this test."""
        patcher = mock.patch.object(model, "complete", new=self)
        patcher.start()
        test_case.addCleanup(patcher.stop)


class MockSittings(QuizSittings):
    """Asking for the mock exam and handing it in, for a test that needs a score.

    The other half of ``QuizSittings``: a paper that is not recorded as a topic
    attempt, is marked from the questions rather than from the form a quiz page
    drew, and exists at most once for a course. The claim this ticket asks about
    is about mock scores, so something has to be able to sit one.
    """

    def take_exam(
        self,
        right: Fraction = Fraction(1),
        *,
        course_id: int = 1,
        moment: date | None = None,
        question: dict[str, Any] | None = None,
    ) -> Fraction:
        """Sit this course's mock exam once, marking ``right`` of it correct.

        Asks for the paper only if there is not one already, because a course
        gets one attempt: asking again would be refused rather than sat, and a
        test that has to know whether its own subject sat first is a test that
        could silently be testing the refusal.
        """
        source = question or DEFAULT_QUESTION
        QuizModel(source).use(self)  # type: ignore[arg-type]
        if not MockExam.objects.filter(course_id=course_id).exists():
            response = self.client.post(  # type: ignore[attr-defined]
                f"/courses/{course_id}/exam/start/", follow=True
            )
            assert response.status_code == 200, "the mock exam was not written"
        day = moment or A_MONDAY
        with on_a_monday(day):
            html = self.exam_page(course_id=course_id)
            asked = re.findall(r'id="question-(\d+)"', html)
            assert asked, "there is no paper to sit"
            correct = round(right * len(asked))
            answers = {}
            for index, question_id in enumerate(asked):
                choices = re.findall(
                    r'value="([^"]*)"',
                    _question_block(html, f'id="question-{question_id}"'),
                )
                assert len(choices) >= 2, "a question offers nothing to get wrong"
                answers[question_id] = choices[0 if index < correct else -1]
            action = re.search(r'action="([^"]+)"', html)
            which = re.search(r'name="exam" value="(\d+)"', html)
            assert action is not None and which is not None, "no form to hand in"
            response = self.client.post(  # type: ignore[attr-defined]
                action.group(1),
                {
                    "nonsense": "",
                    "exam": which.group(1),
                    **{f"q-{key}": value for key, value in answers.items()},
                },
                follow=True,
            )
            assert response.status_code == 200, "the paper was not accepted"
            assert 'class="error"' not in response.content.decode(), (
                "the paper was refused"
            )
        return Fraction(correct, len(asked))

    def exam_page(self, course_id: int = 1) -> str:
        """The exam page for one course, as the student opens it."""
        response = self.client.get(f"/courses/{course_id}/exam/")  # type: ignore[attr-defined]
        assert response.status_code == 200, "the exam page did not open"
        return flat(response.content.decode())


#: A question and an answer the eigenvalue fixture's paragraphs really contain, so
#: the fake model has something true to write about whichever topic it is asked
#: for. Named here rather than repeated per test module because it is the fixture
#: that goes with this helper, not with any one file's course.
DEFAULT_QUESTION = fake_question(
    1, "What is the vector v called in Av = lambda v?", answer="eigenvector"
)
