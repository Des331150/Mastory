"""What serving one student costs, and the work that was never asked for.

Every test here goes in through HTTP and fakes only ``material.model.complete``,
so the rows this page shows are written by the same code that reads material in
and writes quizzes in a real run. Every figure is read off the page rather than
off the table, because a cost nobody can read is a claim rather than a
measurement - and because the point of the ticket is that the number comes out
of event data with nobody asked how they are getting on.

The rule this file exists to pin down is one sentence: **nothing is generated
until the student asks for it, and nothing is written twice for the same
artefact.** The rows are the evidence, so the tests count them. A page that
generated a week of quizzes would show a row per topic before the student had
opened one quiz, and a quiz rewritten on every visit would show a row a time -
neither of which a student is billed for and neither of which this product says
it does.

The calls in a row are asserted against the prompts the fake model saw rather
than against a number written down here, so the row cannot drift from the work
it was measuring: a row claiming five calls when the model was asked six times is
a cost figure for something that did not happen.
"""

import email.message
import json
import re
import time
import urllib.error
import urllib.request
from email.utils import formatdate
from typing import Any
from unittest import mock

from django.test import TestCase, override_settings

from material import model
from material.tests.helpers import (
    CELL_BIOLOGY_PDF,
    DEFAULT_QUESTION,
    LINEAR_ALGEBRA_PDF,
    QuizModel,
    attribute,
    fake_topic,
    flat,
    panel,
    pdf_upload,
    report_call,
    topic_pk,
    use_fake_model,
    use_temporary_media_root,
)

TOPICS = (
    ("Eigenvalues", [2, 3]),
    ("Eigenspaces", [4]),
)

#: What the provider charges, in dollars per million tokens. Set by whoever runs
#: the server, and overridden here so the money on the page is a number this test
#: checks rather than a number this test made up. A dollar a token is not a real
#: price and is not meant to be: at that rate the figure the page prints is the
#: count of tokens, so the pricing can be checked through the page without the
#: test re-deriving the arithmetic it is checking.
A_PRICE = 1_000_000.0


class SpendTestCase(TestCase):
    """One course uploaded, its topic path confirmed, and no artefact written."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        claims = [fake_topic(title, slides) for title, slides in TOPICS]
        with use_fake_model(*claims):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/confirm/")

    # -- reading the page ------------------------------------------------------

    def page(self, course: int = 1) -> str:
        response = self.client.get(f"/courses/{course}/cost/")
        self.assertEqual(response.status_code, 200)
        return flat(response.content.decode())

    def rows(self, page: str | None = None, kind: str = "") -> list[str]:
        """The rows of one kind on the page, in the order they happened.

        Cut at the closing tag of a row rather than at the next one, so a row
        holding more than a line is read whole: a test that only ever saw the
        first line would pass on a page that had stopped saying what a row cost.
        """
        shown = self.page() if page is None else page
        return [
            chunk.split("</li>")[0]
            for chunk in shown.split('<li class="spend-row"')[1:]
            if not kind or f'data-kind="{kind}"' in chunk
        ]

    def kinds(self, page: str | None = None) -> list[str]:
        """What work the page has rows for, in the order it happened."""
        return [attribute(row, "data-kind") for row in self.rows(page)]

    # -- reaching the artefacts the way a student reaches them -----------------

    def topic_id(self, title: str) -> str:
        html = self.client.get("/courses/1/topics/").content.decode()
        return topic_pk(html, title)

    def quiz_url(self, title: str) -> str:
        return f"/courses/1/topics/{self.topic_id(title)}/quiz/"

    def open_quiz(self, title: str) -> QuizModel:
        """Open this topic's quiz with a fake that remembers what it was asked."""
        fake = QuizModel(DEFAULT_QUESTION)
        fake.use(self)
        response = self.client.get(self.quiz_url(title))
        self.assertEqual(response.status_code, 200)
        return fake

    def start_exam(self, fake: QuizModel) -> None:
        """Ask for the mock exam, which is the only thing that writes one."""
        fake.use(self)
        response = self.client.post("/courses/1/exam/start/", follow=True)
        self.assertEqual(response.status_code, 200)


class RecordedTestCase(SpendTestCase):
    """Reading material in and working out the topic path are both written down."""

    def test_reading_material_in_is_written_down_and_costs_no_model_call(self) -> None:
        """Ingestion is work too, and it is not model work: converting a PDF is
        seconds on this server and nothing on anyone else's bill, so the row for
        it says so rather than claiming a call that was never made."""
        row = self.rows(self.page(), "ingest")[0]

        self.assertEqual(attribute(row, "data-calls"), "0")
        self.assertEqual(attribute(row, "data-tokens"), "0")
        self.assertIn("Reading material in", row)
        self.assertGreater(
            float(attribute(row, "data-seconds")),
            0.0,
            "reading a PDF takes time, and a row recording none would not be "
            "recording the half of the cost that is not a model call",
        )

    def test_one_row_is_written_for_each_file_read_in(self) -> None:
        """Two files, two rows. A page that summed them would still be right
        about the total and would lose the one a student asks about when the
        upload took four minutes."""
        self.client.post(
            "/courses/new/",
            {"files": [pdf_upload(LINEAR_ALGEBRA_PDF), pdf_upload(CELL_BIOLOGY_PDF)]},
        )
        rows = self.rows(self.page(2), "ingest")

        self.assertEqual(len(rows), 2)
        self.assertIn("math-201-eigenvalues.pdf", rows[0])
        self.assertIn("bio-110-membrane-transport.pdf", rows[1])
        self.assertEqual(len(self.rows(self.page(1), "ingest")), 1)

    def test_working_out_the_topic_path_is_written_down_as_the_calls_it_took(self) -> None:
        """Inference is the first thing a student causes the model to do, and it
        is one call: reading a course in and guessing at its topics is a single
        question, not one per slide."""
        row = self.rows(self.page(), "topics")[0]

        self.assertEqual(attribute(row, "data-calls"), "1")
        self.assertIn("Working out the topic path", row)
        self.assertGreater(int(attribute(row, "data-tokens")), 0)

    def test_the_page_says_what_the_total_is_of(self) -> None:
        """The total is the rows added up, and nothing else. A total that could
        fall out of step with the work it was totalled from would be a second
        answer to what this course cost."""
        page = self.page()
        totals = panel(page, "ledger")

        self.assertEqual(
            int(attribute(totals, "data-calls")),
            sum(int(attribute(row, "data-calls")) for row in self.rows(page)),
        )
        self.assertEqual(
            int(attribute(totals, "data-tokens")),
            sum(int(attribute(row, "data-tokens")) for row in self.rows(page)),
        )
        self.assertIn("Nothing on this page was written before you asked for it", page)


class OnDemandTestCase(SpendTestCase):
    """Nothing is generated until the student reaches it."""

    def test_no_quiz_is_written_before_the_student_opens_one(self) -> None:
        """A course of twenty topics is twenty generation calls, and a week of
        quizzes written on Monday for a student who stops on Tuesday is the
        whole per-student cost this product says it does not have. The page is
        the evidence: two topics, a confirmed path, a week they could have built,
        and no quiz row between them.

        Asserted against the model as well as against the rows, because the rows
        are this change's own record: a generation written on some path that
        forgot to write itself down would leave the count at two and this test
        passing.
        """
        fake = QuizModel(DEFAULT_QUESTION)
        fake.use(self)
        self.client.post(
            "/courses/1/plan/settings/",
            {"exam_date": "", "days_per_week": "3", "hours_per_week": "12"},
            follow=True,
        )

        self.assertEqual(fake.prompts, [], "building the week asked the model")
        self.assertEqual(self.kinds(), ["ingest", "topics"])

    def test_no_mock_exam_is_written_until_the_student_asks_for_one(self) -> None:
        """Asking to see the exam page is not asking for an exam. A paper is
        written per topic of the course, so a page that wrote one on the way in
        would have the student waiting on a sitting they never started."""
        fake = QuizModel(DEFAULT_QUESTION)
        fake.use(self)
        response = self.client.get("/courses/1/exam/")
        self.assertEqual(response.status_code, 200)

        self.assertEqual(fake.prompts, [], "looking at the exam page asked the model")
        self.assertEqual(self.kinds(), ["ingest", "topics"])

    def test_opening_a_quiz_writes_exactly_one_row_for_it(self) -> None:
        fake = self.open_quiz("Eigenvalues")

        rows = self.rows(self.page(), "quiz")
        self.assertEqual(len(rows), 1)
        self.assertEqual(attribute(rows[0], "data-calls"), str(len(fake.prompts)))
        self.assertIn("Eigenvalues", rows[0])

    def test_reopening_a_quiz_adds_no_row_and_asks_the_model_nothing(self) -> None:
        """The artefact is kept once the student has reached it, so a second
        visit is a page load and nothing else. The row count and the prompts are
        both asserted: a row that stopped being written would pass on the count
        alone if the page quietly wrote the questions again instead."""
        written = self.open_quiz("Eigenvalues")
        before = len(written.prompts)

        again = QuizModel(DEFAULT_QUESTION)
        again.use(self)
        response = self.client.get(self.quiz_url("Eigenvalues"))
        self.assertEqual(response.status_code, 200)

        self.assertEqual(again.prompts, [], "reopening a quiz asked the model again")
        self.assertEqual(len(written.prompts), before)
        self.assertEqual(len(self.rows(self.page(), "quiz")), 1)

    def test_asking_for_different_questions_is_written_down_as_its_own_work(self) -> None:
        """A retake writes a new quiz because the questions have to be different,
        and that is model work the student asked for. Counting it against the
        sitting rather than as its own row would make a week of retaking look
        like one cheap quiz."""
        self.open_quiz("Eigenvalues")

        fake = QuizModel(DEFAULT_QUESTION)
        fake.use(self)
        response = self.client.post(
            f"{self.quiz_url('Eigenvalues')}rewrite/", follow=True
        )
        self.assertEqual(response.status_code, 200)

        rows = self.rows(self.page(), "quiz")
        self.assertEqual(len(rows), 2)
        self.assertEqual(attribute(rows[1], "data-calls"), str(len(fake.prompts)))

    def test_asking_for_a_mock_exam_writes_one_row_naming_the_paper(self) -> None:
        fake = QuizModel(DEFAULT_QUESTION)
        self.start_exam(fake)

        rows = self.rows(self.page(), "exam")
        self.assertEqual(len(rows), 1)
        self.assertEqual(attribute(rows[0], "data-calls"), str(len(fake.prompts)))
        self.assertIn("mock exam", rows[0])

    def test_nothing_is_written_down_when_nothing_was_done(self) -> None:
        """A page is not work. Every page in this product is read as often as it
        is acted on, and one that wrote a row a visit would make the cost of
        looking at the cost of the product a cost of its own."""
        for _ in range(3):
            self.assertEqual(len(self.rows()), 2)


class PricedTestCase(SpendTestCase):
    """What a call cost, once somebody tells us what a call costs."""

    def priced(self) -> Any:
        return override_settings(MODEL_USD_PER_MILLION_TOKENS=A_PRICE)

    def test_the_tokens_the_model_reported_are_kept_and_priced(self) -> None:
        """A provider says what each call moved with the reply, and that is the
        only honest way to price a call: a figure worked out from the size of our
        own request would be an estimate wearing a total's clothes.

        Priced at a dollar a token so that the figure on the page *is* the count
        the provider reported, which is the only way this assertion can tell
        pricing apart from arithmetic the test did itself.
        """
        self.open_quiz("Eigenvalues")

        with self.priced():
            row = self.rows(self.page(), "quiz")[0]
        tokens = int(attribute(row, "data-tokens"))

        self.assertGreater(tokens, 0)
        self.assertEqual(attribute(row, "data-cost"), f"{tokens:.6f}")

    def test_a_model_with_no_price_configured_is_shown_unpriced(self) -> None:
        """Nobody has told this server what its model charges, so the page shows
        the tokens and says why there is no money. Printing 0.00 would say the
        course was free, which is the one thing a cost page must never claim."""
        self.open_quiz("Eigenvalues")
        page = self.page()

        self.assertGreater(
            int(attribute(self.rows(page, "quiz")[0], "data-tokens")), 0
        )
        self.assertIn("No price is configured for this model", page)

    def test_a_call_that_reports_nothing_is_never_costed_at_nothing(self) -> None:
        """A provider that answers without saying what it moved leaves the cost
        unknown, which is a different fact from a call that cost nothing. The row
        leaves the number out rather than printing a zero, and the page says so
        rather than quietly costing the course at nothing."""
        with use_fake_model(fake_topic(*TOPICS[0]), silent=True):
            self.client.post("/courses/1/topics/infer/")

        with self.priced():
            page = self.page()
        inferred = self.rows(page, "topics")[-1]

        self.assertEqual(attribute(inferred, "data-calls"), "1")
        self.assertEqual(attribute(inferred, "data-tokens"), "")
        self.assertEqual(attribute(inferred, "data-cost"), "")
        self.assertIn("did not report what its calls moved", page)

    def test_a_generation_that_failed_is_still_written_down(self) -> None:
        """A refused quiz is the expensive sort: it took the wall clock and gave
        the student nothing. Recording only the calls that came back with
        questions would price a failing week at nothing, which is the week a
        student most needs the number.

        The call is counted and claims nothing was moved, which is what a
        provider that refuses tells you: one call happened and no answer came
        back to say what it cost.
        """

        def answering(prompt: str) -> str:
            report_call(prompt, "", silent=True)
            raise model.ModelUnavailable("The model would not answer.", "Try again.")

        with mock.patch.object(model, "complete", answering):
            response = self.client.get(self.quiz_url("Eigenvalues"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("The model would not answer", flat(response.content.decode()))

        rows = self.rows(self.page(), "quiz")
        self.assertEqual(len(rows), 1, "the generation that failed was not written down")
        self.assertEqual(attribute(rows[0], "data-calls"), "1")
        self.assertEqual(attribute(rows[0], "data-tokens"), "")


class _Reply:
    """One HTTP response as ``urlopen`` hands it back: a context manager over bytes."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> "_Reply":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class AnsweredTestCase(TestCase):
    """What a provider says a call moved, read out of the one reply that says it.

    Driven here rather than through the client, and that is the whole point: the
    rest of the suite stands a fake in for ``complete`` itself, so a real reply is
    never parsed anywhere else and a cost page fed by a real provider would be the
    one page in this product nothing had ever run.
    """

    #: A configured endpoint. The host does not resolve on purpose: the test never
    #: opens a socket, it hands ``urlopen`` the reply to hand back.
    SERVED = {
        "MODEL_BASE_URL": "http://model.invalid",
        "MODEL_NAME": "a-model",
        "MODEL_API_KEY": "a-key",
    }

    def call(self, usage: dict[str, Any] | None = None) -> model.Meter:
        """One real call whose reply carries ``usage``, metered."""
        body: dict[str, Any] = {"choices": [{"message": {"content": "{}"}}]}
        if usage is not None:
            body["usage"] = usage
        with (
            override_settings(**self.SERVED),
            mock.patch.object(urllib.request, "urlopen", return_value=_Reply(body)),
            model.measuring() as meter,
        ):
            model.complete("a prompt")
        return meter

    def test_a_reply_saying_what_it_moved_is_recorded(self) -> None:
        """The only honest way to price a call is what the provider says it cost,
        and this is the one place that reply is read."""
        meter = self.call({"total_tokens": 812})

        self.assertEqual(meter.calls, 1)
        self.assertEqual(meter.tokens, 812)

    def test_a_reply_carrying_no_count_is_unknown_rather_than_free(self) -> None:
        """A provider that answers without saying what it moved has not given us
        a cost of nothing, and a ledger that read the absence as zero would price
        a whole course for free on the strength of a missing field."""
        self.assertIsNone(self.call().tokens)

    def test_a_call_that_never_came_back_is_still_counted(self) -> None:
        """The expensive one. It took the wall clock and the provider's money and
        returned nothing, so a ledger counting only the calls that answered would
        price a failing week at nothing."""
        with (
            override_settings(**self.SERVED),
            mock.patch.object(
                urllib.request, "urlopen", side_effect=urllib.error.URLError("no route")
            ),
            model.measuring() as meter,
        ):
            with self.assertRaises(model.ModelUnavailable):
                model.complete("a prompt")

        self.assertEqual(meter.calls, 1)
        self.assertIsNone(meter.tokens)


class ThrottlingTests(AnsweredTestCase):
    """A provider that is busy is waited for, and is not reported as unreachable.

    Found on the first real course (issue 14): one 233-page course needed about
    fifty calls inside a few minutes, which is more than a free tier allows per
    minute. Read as an ordinary failure, throttling dropped every question in
    the second half of the course and told the student the model could not be
    reached - a reason that sends them to check their connection instead of
    waiting a minute.
    """

    @staticmethod
    def _throttled(headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            "http://model.invalid",
            429,
            "Too Many Requests",
            _headers(headers or {}),
            None,
        )

    #: What one call waited and what it was charged, kept on the case so a test
    #: that asserts the refusal can still read the waits that led to it.
    slept: list[float]
    meter: model.Meter

    def call_through(
        self,
        *errors: BaseException,
        answer: dict[str, Any] | None = None,
    ) -> tuple[str, model.Meter, list[float]]:
        """One call metered, where the provider refuses ``errors`` before answering."""
        body = answer or {"choices": [{"message": {"content": "{}"}}],
                          "usage": {"total_tokens": 20}}
        slept: list[float] = []
        self.slept = slept
        refusals = list(errors)

        def refusing(*_: Any, **__: Any) -> Any:
            if refusals:
                raise refusals.pop(0)
            return _Reply(body)

        with (
            override_settings(**self.SERVED),
            mock.patch.object(urllib.request, "urlopen", side_effect=refusing),
            mock.patch.object(time, "sleep", slept.append),
            model.measuring() as meter,
        ):
            self.meter = meter
            return model.complete("a prompt"), meter, slept

    def test_a_throttled_call_is_tried_again_rather_than_dropped(self) -> None:
        reply, meter, slept = self.call_through(
            self._throttled(), self._throttled()
        )

        self.assertEqual(json.loads(reply), {})
        self.assertEqual(
            meter.calls, 3, "the ledger counts requests made, not intentions"
        )
        # The two refused requests did not say what they moved and never will,
        # so the whole call is of unknown cost rather than the one known answer
        # dressed up as a total.
        self.assertIsNone(meter.tokens)
        self.assertEqual(len(slept), 2, "it did not wait between the refusals")
        self.assertEqual(sorted(slept), [1.0, 2.0], "the wait did not grow")

    def test_a_wait_named_as_a_date_is_believed_too(self) -> None:
        """``Retry-After`` is allowed to be an HTTP date, and a provider that
        sends one is asking for a specific moment, not a number of seconds."""
        when = formatdate(time.time() + 20, usegmt=True)
        _, _, slept = self.call_through(
            self._throttled({"Retry-After": when}),
            answer={"choices": [{"message": {"content": "{}"}}]},
        )

        self.assertEqual(len(slept), 1)
        self.assertGreater(slept[0], 15.0, "it retried before the time it was given")
        self.assertLessEqual(slept[0], 20.0)

    def test_a_wait_further_off_than_the_budget_is_not_waited_at_all(self) -> None:
        """A provider naming an hour is told, plainly, rather than being waited
        on: the page asks a student for a minute and this is a minute."""
        throttled = self._throttled({"Retry-After": "3600"})
        with self.assertRaises(model.ModelUnavailable) as raised:
            self.call_through(*[throttled] * model.THROTTLE_ATTEMPTS)

        self.assertIn("too busy", str(raised.exception))
        self.assertEqual(self.slept, [], "it waited on a wait it could not afford")

    def test_the_budget_is_on_the_total_not_on_each_wait(self) -> None:
        """A provider asking for a minute on every refusal must not be able to
        buy four minutes of a student watching a spinner.

        Capping each sleep instead of the total made this 240 seconds against a
        stated budget of 75.
        """
        throttled = self._throttled({"Retry-After": "60"})
        with self.assertRaises(model.ModelUnavailable) as raised:
            self.call_through(*[throttled] * model.THROTTLE_ATTEMPTS)

        self.assertIn("too busy", str(raised.exception))
        self.assertLessEqual(
            sum(self.slept),
            model.RETRY_BUDGET_SECONDS,
            "the whole of the waiting has to fit the budget",
        )
        self.assertEqual(
            self.meter.calls, 1, "a wait the budget cannot afford is not taken at all"
        )

    def test_a_wait_the_budget_cannot_afford_is_not_taken_at_all(self) -> None:
        """The page tells a student to wait a minute. This product must not then
        hold them for longer than that over a provider's wishes."""
        throttled = self._throttled({"Retry-After": "3600"})
        with self.assertRaises(model.ModelUnavailable):
            self.call_through(*[throttled] * model.THROTTLE_ATTEMPTS)

        self.assertLessEqual(sum(self.slept), model.RETRY_BUDGET_SECONDS)

    def test_a_throttled_call_that_is_given_no_answer_is_not_waited_on_at_all(self) -> None:
        """An error this product is asked to hold on to is closed, not left for
        a collector: a throttled run makes these by the dozen."""
        self.closed: list[bool] = []
        self.slept = []
        throttled = self._throttled()
        throttled.close = lambda: self.closed.append(True)  # type: ignore[method-assign]
        with override_settings(
            **self.SERVED
        ), mock.patch.object(
            time, "sleep", self.slept.append
        ), mock.patch.object(urllib.request, "urlopen", side_effect=throttled):
            with self.assertRaises(model.ModelUnavailable):
                model.complete("a prompt")

        self.assertTrue(self.closed, "the error holding the response was not closed")

    def test_a_wait_that_is_not_a_number_is_not_waited_on(self) -> None:
        """``Retry-After: nan`` parses as a float and compares False to
        everything, which would carry a nan into ``sleep`` and raise there."""
        _, _, slept = self.call_through(
            self._throttled({"Retry-After": "nan"}),
            answer={"choices": [{"message": {"content": "{}"}}]},
        )

        self.assertEqual(slept, [1.0], "it fell back on the backoff, not the nan")

    def test_a_body_that_is_not_text_is_unreachable_rather_than_a_crash(self) -> None:
        """A provider answering in bytes this product cannot read has not
        answered, and the request was still made."""
        with (
            override_settings(**self.SERVED),
            mock.patch.object(
                urllib.request, "urlopen", return_value=_Bytes(b"\xff\xfe not utf-8")
            ),
            model.measuring() as meter,
        ):
            with self.assertRaises(model.ModelUnavailable) as raised:
                model.complete("a prompt")

        self.assertIn("could not be reached", str(raised.exception))
        self.assertEqual(meter.calls, 1, "the request was made whether or not it read")

    def test_a_provider_that_keeps_refusing_says_it_is_busy(self) -> None:
        throttled = self._throttled()
        with self.assertRaises(model.ModelUnavailable) as raised:
            self.call_through(*[throttled] * model.THROTTLE_ATTEMPTS)

        self.assertIn("too busy", str(raised.exception))
        self.assertNotIn("could not be reached", str(raised.exception))

    def test_the_providers_own_wait_is_believed(self) -> None:
        """``Retry-After`` is the only number here that knows how loaded the
        provider actually is, so it beats our guess."""
        _, _, slept = self.call_through(
            self._throttled({"Retry-After": "9"}),
            answer={"choices": [{"message": {"content": "{}"}}]},
        )

        self.assertEqual(slept, [9.0])

    def test_a_refusal_that_is_not_throttling_is_not_waited_for(self) -> None:
        """A broken endpoint is a different fault from a busy one, and a server
        error is not going to clear itself by waiting for it."""
        with self.assertRaises(model.ModelUnavailable) as raised:
            self.call_through(
                urllib.error.HTTPError(
                    "http://model.invalid",
                    500,
                    "Server Error",
                    email.message.Message(),
                    None,
                )
            )

        self.assertIn("could not be reached", str(raised.exception))


class _Bytes:
    """A reply whose bytes are not valid text, which a provider can still send."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_Bytes":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _headers(pairs: dict[str, str]) -> email.message.Message:
    """An ``email.message.Message`` carrying ``pairs``, which is what an
    ``HTTPError`` holds its headers in.

    Built rather than faked because urllib reads ``Retry-After`` off it and this
    test is about what urllib sees.
    """
    message = email.message.Message()
    for name, value in pairs.items():
        message[name] = value
    return message