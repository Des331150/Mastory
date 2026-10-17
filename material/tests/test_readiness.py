"""The readiness report: the end of the course, and the figure it refuses to print.

Every test here goes in through HTTP and fakes only ``material.model.complete``,
so the quiz, the marking, the mastery weighting and the report are all the real
application, and every number is read off the page the student is looking at -
because a readiness figure nobody can see is a claim rather than a feature.

The rule this file exists to pin down is one sentence: **the report prints a
readiness figure only when enough of the course has been weighed, and says so in
words when it does not.** A partial-data percentage is worse than no number,
because a student will act on it, so the refusal is asserted as a refusal and the
figure is asserted as a weighted mean of what was actually sat.

The clock is pinned to a known Monday wherever the day a thing happened is part of
what is being asserted, and the per-topic figures are read off the page and used to
compute the expected mean rather than hardcoded, so the assertion is about the
weighting and not about a number that moves when a fixture does.
"""

import re
from contextlib import contextmanager
from datetime import date, datetime, time
from fractions import Fraction
from html import unescape
from typing import Iterator
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from django.utils.timezone import make_aware

from material import planning
from material.tests.helpers import (
    LINEAR_ALGEBRA_PDF,
    QuizModel,
    fake_question,
    fake_topic,
    pdf_upload,
    topic_pk,
    use_fake_model,
    use_temporary_media_root,
)

#: Four topics of one course, each pointing at a slide of its own. Four is two
#: more than the report's halfway mark and two less than the whole course, so one
#: course can show both states of the figure without two fixtures.
TOPICS = (
    ("Eigenvalues", [1]),
    ("Eigenspaces", [2]),
    ("Diagonalisation", [3]),
    ("Cayley-Hamilton", [4]),
)
NAMES = [title for title, _ in TOPICS]

#: A Monday, so the day a topic was finished on can be read off the report.
A_MONDAY = date(2026, 11, 2)

#: A question and an answer the fixture's paragraphs really contain, so the fake
#: model has something true to write about on whichever topic it is asked for.
FROM_THE_MATERIAL = fake_question(
    1, "What is the vector v called in Av = lambda v?", answer="eigenvector"
)

#: What the report must never say: a promise about the paper.
PREDICTIONS = ("you will pass", "the exam will be easy", "guaranteed")


def flat(text: str) -> str:
    """The page as a student reads it: no wrapping, no entities.

    Entities are decoded because the report says "this course's" and the page it
    is asserted on says ``this course&#x27;s``; a test that has to know about the
    escaping would be testing the template engine rather than the report.
    """
    return " ".join(unescape(text).split())


@contextmanager
def on_a_monday(moment: date = A_MONDAY) -> Iterator[None]:
    """Do the student's work as though they were living on a known day.

    Patches the two clocks a sitting is stamped with and not one: ``planning``
    stamps the day a topic was finished, and the model's ``auto_now_add`` stamps
    the moment the sitting was taken. A report that dates one and not the other
    would put a student's quiz on a day their work is not logged, which is the
    pair of answers this product keeps refusing to print.
    """
    with (
        mock.patch.object(planning, "today", return_value=moment),
        mock.patch(
            "django.db.models.fields.timezone.now", return_value=_at(moment)
        ),
    ):
        yield


def _at(day: date) -> datetime:
    """Midday on that day, as an aware moment, for the row the sitting writes."""
    return make_aware(datetime.combine(day, time(hour=12)), timezone.get_default_timezone())


class ReportTestCase(TestCase):
    """One course uploaded, its topic path confirmed, and no sittings yet."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})

    def build_course(self, *claims: tuple[str, list[int]]) -> None:
        """Infer and confirm a topic path, the way the student does it."""
        with use_fake_model(*[fake_topic(title, slides) for title, slides in claims]):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/confirm/")

    def confirm_four(self) -> None:
        self.build_course(*TOPICS)

    # -- reaching the report and the quizzes ------------------------------------

    def page(self) -> str:
        """The readiness report, as the student reads it."""
        response = self.client.get("/courses/1/readiness/")
        self.assertEqual(response.status_code, 200)
        return flat(response.content.decode())

    def topic_id(self, title: str) -> str:
        return topic_pk(self.client.get("/courses/1/topics/").content.decode(), title)

    def sit(self, title: str, right: Fraction, *, moment: date = A_MONDAY) -> Fraction:
        """Sit this topic's quiz once, marking ``right`` of its questions correct.

        Returns the score that sitting should have recorded, as the exact fraction
        of the questions on the page rather than as a float, so that a test
        computing an expected weighted mean is doing arithmetic rather than
        rounding twice.
        """
        fake = QuizModel(FROM_THE_MATERIAL)
        fake.use(self)
        with on_a_monday(moment):
            html = flat(self.client.get(self.quiz_url(title)).content.decode())
            questions = re.findall(r'id="question-(\d+)"', html)
            self.assertTrue(questions, f"no questions on {title}'s quiz to answer")
            correct = round(right * len(questions))
            answers = {}
            for index, question in enumerate(questions):
                choices = re.findall(
                    r'value="([^"]*)"', self.block(html, f'id="question-{question}"')
                )
                self.assertGreaterEqual(
                    len(choices), 2, f"question {question} offers nothing to get wrong"
                )
                answers[question] = choices[0 if index < correct else -1]
            action = re.search(r'action="([^"]+)"', html)
            which = re.search(r'name="quiz" value="(\d+)"', html)
            assert action is not None and which is not None, "no form to hand in"
            response = self.client.post(
                action.group(1),
                {
                    "quiz": which.group(1),
                    **{f"q-{key}": value for key, value in answers.items()},
                },
                follow=True,
            )
            self.assertEqual(response.status_code, 200)
        return Fraction(correct, len(questions))

    def sit_again(self, title: str, right: Fraction, *, moment: date = A_MONDAY) -> Fraction:
        """Take a topic's quiz a second time, on new questions."""
        fake = QuizModel(FROM_THE_MATERIAL)
        fake.use(self)
        with on_a_monday(moment):
            response = self.client.post(
                f"{self.quiz_url(title)}rewrite/", follow=True
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(
                re.findall(r'id="question-\d+"', response.content.decode()),
                f"the rewrite of {title} produced no questions to sit",
            )
        return self.sit(title, right, moment=moment)

    def quiz_url(self, title: str) -> str:
        return f"/courses/1/topics/{self.topic_id(title)}/quiz/"

    def block(self, html: str, opening: str) -> str:
        """The question that carries ``opening``, cut out of the page.

        Cut at the next question rather than at the next list item, because a
        question's choices are list items of their own and a shorter cut would
        leave one option to answer with.
        """
        for chunk in html.split('<li class="question')[1:]:
            if opening in chunk:
                return chunk.split('<li class="question')[0]
        raise AssertionError(f"the page has no question with {opening}")

    def reweigh(self, title: str, weight: int) -> None:
        """Say how much of the week a topic is worth, as the student does."""
        response = self.client.post(
            f"/courses/1/topics/{self.topic_id(title)}/weight/",
            {"weight": str(weight)},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

    # -- reading the report ----------------------------------------------------

    def figure(self, page: str) -> str:
        """The readiness figure the page gives, or ``unmeasurable``.

        Read off the panel rather than searched for anywhere on the page: the same
        page carries every topic's own mastery, and a whole-page search would
        answer with whichever number came first.
        """
        found = re.search(
            r'<section class="panel readiness[^"]*" data-readiness="([^"]+)"',
            page,
        )
        assert found is not None, "the report has no readiness panel"
        return found.group(1)

    def coverage(self, page: str) -> tuple[int, int]:
        """How many of the course's topics the report says have been weighed."""
        found = re.search(r'data-covered="(\d+)" data-total="(\d+)"', page)
        assert found is not None, "the report says how much of the course it covers"
        return int(found.group(1)), int(found.group(2))

    def row(self, page: str, title: str) -> str:
        """This topic's row on the report, found the way a student finds it."""
        for chunk in page.split('<li class="standing-row"')[1:]:
            if re.search(rf'>{re.escape(title)}</p>', chunk):
                return chunk.split("</li>")[0]
        raise AssertionError(f"the report has no row for {title!r}")

    def mastery(self, page: str, title: str) -> str:
        """The mastery the report shows for one topic, or ``unmeasured``."""
        found = re.search(r'data-mastery="([^"]+)"', self.row(page, title))
        assert found is not None, f"the report says nothing about {title}'s mastery"
        return found.group(1)

    def weakest(self, page: str) -> list[str]:
        """The topics the report ranks as the ones to revise first, in order."""
        return re.findall(
            r'<p class="weakest-topic">([^<]+)</p>', page
        )

    def day_lines(self, page: str) -> list[str]:
        """One line per day the report says the student studied."""
        return re.findall(r'<li class="log-entry"[^>]*>(.*?)</li>', page)


class RefusesToLieTestCase(ReportTestCase):
    """The figure, and the refusal of one.

    The acceptance criterion this file is named for: with mastery data covering
    too few topics, the report states that readiness is not measurable instead of
    printing a number.
    """

    def setUp(self) -> None:
        super().setUp()
        self.confirm_four()

    def test_one_sitting_of_four_topics_refuses_the_figure(self) -> None:
        self.sit("Eigenvalues", Fraction(1))
        page = self.page()
        self.assertEqual(self.figure(page), "unmeasurable")
        self.assertEqual(self.coverage(page), (1, 4))
        self.assertIn("not measurable", page)
        self.assertIn("weighed 1 of this course's 4 topics", page)
        self.assertIn("needs at least 2 of them", page)

    def test_the_refusal_prints_no_percentage_at_all(self) -> None:
        """A refused figure must not leave a number behind to be mistaken for one."""
        self.sit("Eigenvalues", Fraction(1))
        page = self.page()
        self.assertNotIn('class="readiness-figure"', page)
        self.assertNotIn("readiness-meter", page)

    def test_four_topics_of_twenty_is_still_no_figure(self) -> None:
        """The case the product names: four sittings of a twenty topic course.

        Twenty topics is the size of a real course rather than a fixture, and four
        of them is where a mean would look most like a result: 4/20 is the figure
        a student would screenshot and believe.
        """
        self.build_course(*[(f"Topic {number}", [1]) for number in range(1, 21)])
        for number in range(1, 5):
            self.sit(f"Topic {number}", Fraction(1))
        page = self.page()
        self.assertEqual(self.figure(page), "unmeasurable")
        self.assertEqual(self.coverage(page), (4, 20))
        self.assertIn("weighed 4 of this course's 20 topics", page)
        self.assertIn("needs at least 10 of them", page)

    def test_half_the_course_is_enough_for_a_figure(self) -> None:
        # Both weighed at one unit each, so that this test is about the halfway
        # mark and not about the weights the pointer map happened to derive.
        self.reweigh("Eigenvalues", 1)
        self.reweigh("Eigenspaces", 1)
        high = self.sit("Eigenvalues", Fraction(1))
        low = self.sit("Eigenspaces", Fraction(0))
        page = self.page()
        expected = round((high + low) / 2 * 100)
        self.assertEqual(self.figure(page), str(expected))
        self.assertNotIn("not measurable", page)

    def test_one_short_of_the_halfway_mark_is_still_refused(self) -> None:
        """A five topic course needs three sat; two is not a figure."""
        self.build_course(*[(f"Topic {number}", [1]) for number in range(1, 6)])
        self.reweigh("Topic 1", 1)
        self.reweigh("Topic 2", 1)
        self.sit("Topic 1", Fraction(1))
        self.sit("Topic 2", Fraction(0))
        page = self.page()
        self.assertEqual(self.figure(page), "unmeasurable")
        self.assertEqual(self.coverage(page), (2, 5))
        self.assertIn("needs at least 3 of them", page)
        self.sit("Topic 3", Fraction(1))
        self.assertEqual(self.coverage(self.page()), (3, 5))
        self.assertNotEqual(self.figure(self.page()), "unmeasurable")


class ReportContentsTestCase(ReportTestCase):
    """What the report shows a student who has been working."""

    def setUp(self) -> None:
        super().setUp()
        self.confirm_four()
        self.high = self.sit("Eigenvalues", Fraction(1))
        self.middle = self.sit("Eigenspaces", Fraction(1, 2))
        self.low = self.sit("Diagonalisation", Fraction(0))

    def test_readiness_is_the_weighted_mean_of_what_was_weighed(self) -> None:
        """Three topics sat, one of them weighed three times as much.

        The weight is the same number the week is built from, so a topic the
        student gave three units of time to has to count for three, and an
        unweighted mean of the same three figures would be a different number.
        """
        # Weights said outright rather than derived from the pointer map, so the
        # expected mean is the one this test writes down and not a number the
        # fixture's slide text happens to produce.
        self.reweigh("Eigenvalues", 1)
        self.reweigh("Eigenspaces", 1)
        self.reweigh("Diagonalisation", 3)
        page = self.page()
        expected = round((self.high * 1 + self.middle * 1 + self.low * 3) / 5 * 100)
        self.assertEqual(self.figure(page), str(expected))
        # And the number that figure would have been without the weights, so a
        # test cannot pass by dropping the weighting and rounding hopefully.
        unweighted = round((self.high + self.middle + self.low) / 3 * 100)
        if expected != unweighted:
            self.assertNotEqual(self.figure(page), str(unweighted))

    def test_every_topic_shows_its_own_mastery(self) -> None:
        page = self.page()
        for title, score in (
            ("Eigenvalues", self.high),
            ("Eigenspaces", self.middle),
            ("Diagonalisation", self.low),
        ):
            self.assertEqual(self.mastery(page, title), str(round(score * 100)))
        self.assertEqual(self.mastery(page, "Cayley-Hamilton"), "unmeasured")

    def test_attempts_per_topic_are_shown_so_a_lucky_sitting_is_visible(self) -> None:
        """One sitting and two sittings must not look like the same claim."""
        self.sit_again("Eigenspaces", Fraction(1))
        page = self.page()
        one = self.row(page, "Diagonalisation")
        self.assertIn("1 sitting,", one)
        self.assertIn("all this rests on", one)
        two = self.row(page, "Eigenspaces")
        self.assertIn("2 sittings", two)
        self.assertNotIn("all this rests on", two)
        self.assertIn('data-sittings="2"', two)

    def test_the_three_weakest_topics_are_ranked_lowest_first(self) -> None:
        page = self.page()
        self.assertEqual(
            self.weakest(page), ["Diagonalisation", "Eigenspaces", "Eigenvalues"]
        )
        self.assertIn("Revise these first", page)

    def test_an_unweighed_topic_is_never_called_weak(self) -> None:
        """Calling a topic nobody has sat "weak" would be a claim, not a ranking."""
        page = self.page()
        self.assertNotIn("Cayley-Hamilton", self.weakest(page))
        self.assertIn("Cayley-Hamilton", page)
        self.assertIn("no sitting at all", page)

    def test_the_dates_studied_are_on_the_report(self) -> None:
        page = self.page()
        days = self.day_lines(page)
        self.assertTrue(days, "the report lists no days studied")
        # Sitting a quiz finishes its topic, and both are stamped on the pinned
        # Monday, so the report must name that day for the three sittings taken.
        monday = self.day_line(page, A_MONDAY)
        self.assertIn("3 topics finished", monday)
        # And the sittings themselves, which are stamped when they happened.
        self.assertTrue(
            any("sitting" in line for line in days),
            f"no day reports a sitting: {days}",
        )

    def test_a_second_sitting_is_a_second_day(self) -> None:
        later = date(2026, 11, 9)
        self.sit_again("Eigenvalues", Fraction(1), moment=later)
        page = self.page()
        self.assertIn("2 sittings", self.row(page, "Eigenvalues"))
        self.assertTrue(self.day_line(page, later))

    def day_line(self, page: str, day: date) -> str:
        """The line for one day of the report, found by the date it carries."""
        found = re.search(
            rf'<li class="log-entry" data-date="{day.isoformat()}">(.*?)</li>', page
        )
        assert found is not None, f"the report has no day for {day.isoformat()}"
        return found.group(1)


class FramingTestCase(ReportTestCase):
    """The figure is revision guidance, not a promise about the exam."""

    def setUp(self) -> None:
        super().setUp()
        self.confirm_four()
        self.sit("Eigenvalues", Fraction(1))
        self.sit("Eigenspaces", Fraction(1, 2))

    def test_the_figure_is_framed_as_what_to_revise(self) -> None:
        page = self.page()
        self.assertIn("not a prediction of your exam", page)
        self.assertIn("where you are weakest", page)
        for promise in PREDICTIONS:
            self.assertNotIn(promise, page.lower())

    def test_the_report_is_one_screen_with_everything_on_it(self) -> None:
        """The artefact a student screenshots: one page, nothing behind a fold."""
        page = self.page()
        for heading in (
            "Your readiness report",
            "Revise these first",
            "Every topic",
            "When you studied",
        ):
            self.assertIn(heading, page)
        self.assertNotIn('href="/courses/1/readiness/', page)


class UnmeasuredCourseTestCase(ReportTestCase):
    """A student who has sat nothing still gets a page, not an error."""

    def setUp(self) -> None:
        super().setUp()
        self.confirm_four()

    def test_an_untouched_course_refuses_the_figure_and_says_what_would_help(self) -> None:
        page = self.page()
        self.assertEqual(self.figure(page), "unmeasurable")
        self.assertEqual(self.coverage(page), (0, 4))
        self.assertIn("never sat", page)
        self.assertIn("what would bring this report to a figure", page)

    def test_the_report_needs_no_week_and_no_exam(self) -> None:
        """The report is about the course, not about this week's sessions."""
        response = self.client.get("/courses/1/readiness/")
        self.assertEqual(response.status_code, 200)


class EmptyCourseTestCase(TestCase):
    """A course whose material has not been given a topic path at all."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})

    def test_a_course_with_no_topics_says_so_instead_of_a_figure(self) -> None:
        response = self.client.get("/courses/1/readiness/")
        self.assertEqual(response.status_code, 200)
        page = flat(response.content.decode())
        self.assertIn('data-readiness="unmeasurable"', page)
        self.assertIn("no topics on it", page)
        self.assertNotIn('class="readiness-figure"', page)