"""Un-finishing a topic the record cannot undo on its own.

Every mark in the product is a toggle: the session button for the state a topic
is in takes it back out. A topic finished by sitting its quiz before a week was
built has no session, so nothing could undo it - this file follows a student
into that dead end and out again, through HTTP, faking only the model the quiz
is written from.
"""

import re
from typing import Any

from django.test import TestCase

from material.tests.helpers import (
    LINEAR_ALGEBRA_PDF,
    QuizModel,
    StudentTestCase,
    fake_question,
    fake_topic,
    pdf_upload,
    topic_pk,
    use_fake_model,
    use_temporary_media_root,
)


TOPICS = (
    ("Eigenvalues", [2, 3]),
    ("Eigenspaces", [4, 5]),
    ("Diagonalisation", [5]),
)

#: A question and an answer the fixture's paragraph really contains, sitting a
#: quiz that finishes a topic for real rather than a stub of one.
FROM_THE_MATERIAL = fake_question(
    1, "What is the vector v called in Av = lambda v?", answer="eigenvector"
)


def flat(text: str) -> str:
    return " ".join(text.split())


class UnfinishTestCase(StudentTestCase):
    """One course uploaded, its topic path confirmed, and no week built."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        self.infer(*(fake_topic(title, slides) for title, slides in TOPICS))
        self.client.post("/courses/1/topics/confirm/")

    # -- the seams everything else here goes through -------------------------

    def infer(self, *topics: Any) -> None:
        with use_fake_model(*topics):
            self.client.post("/courses/1/topics/infer/")

    def topic_id(self, title: str = "Eigenvalues") -> str:
        return topic_pk(self.client.get("/courses/1/topics/").content.decode(), title)

    def set_availability(self, *, days: int, hours: int, exam: str = "") -> None:
        self.client.post(
            "/courses/1/plan/settings/",
            {"exam_date": exam, "days_per_week": str(days), "hours_per_week": str(hours)},
        )

    def plan_page(self) -> str:
        response = self.client.get("/courses/1/plan/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def quiz_page(self) -> str:
        response = self.client.get(f"/courses/1/topics/{self.topic_id()}/quiz/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def sit_quiz(self) -> None:
        """Write a quiz and mark a whole sitting right, as the student would."""
        model = QuizModel(FROM_THE_MATERIAL)
        model.use(self)
        written = self.client.post(
            f"/courses/1/topics/{self.topic_id()}/quiz/rewrite/", follow=True
        ).content.decode()
        which = re.search(r'name="quiz" value="(\d+)"', written)
        assert which is not None, "the quiz form does not say which quiz it is for"
        answers = {}
        for block in written.split('<li class="question')[1:]:
            question = re.search(r'id="question-(\d+)"', block)
            option = re.search(r'value="([^"]*)"', block)
            assert question is not None and option is not None, "a question with no place for an answer"
            answers[question.group(1)] = option.group(1)
        self.client.post(
            f"/courses/1/topics/{self.topic_id()}/quiz/submit/",
            {"quiz": which.group(1), **{f"q-{key}": value for key, value in answers.items()}},
            follow=True,
        )

    def press_standing_button(self) -> None:
        """Press the button the finished topic's standing row carries."""
        self.client.post(f"/courses/1/topics/{self.topic_id()}/mark/", follow=True)

    def standing(self, html: str, title: str = "Eigenvalues") -> str:
        """The standing row of one topic, cut out of the plan page."""
        row = next(
            block
            for block in html.split('<li class="standing-row"')[1:]
            if title in block
        )
        return flat(next(b for b in row.split('<p class="standing-complete"')[1:] if b))

class ATopicFinishedByItsQuizTests(UnfinishTestCase):
    def test_the_finished_topic_is_offered_the_button_the_session_would_have(self) -> None:
        self.sit_quiz()
        html = self.plan_page()
        self.assertIn("finished", self.standing(html))
        self.assertIn("Put this back into your work", self.standing(html))

    def test_pressing_the_button_puts_the_topic_back_into_outstanding_work(self) -> None:
        self.sit_quiz()
        self.press_standing_button()
        self.assertIn("not finished", self.standing(self.plan_page()))

    def test_pressing_it_again_puts_the_topic_back_where_it_was(self) -> None:
        self.sit_quiz()
        self.press_standing_button()
        self.press_standing_button()
        self.assertIn("finished", self.standing(self.plan_page()))

    def test_unfinishing_leaves_the_sittings_alone(self) -> None:
        self.sit_quiz()
        self.press_standing_button()
        self.assertIn("from 1 sitting", self.quiz_page())

    def test_a_topic_that_has_a_session_keeps_its_own_button_only(self) -> None:
        """A session carries the toggle already; the standing row adds nothing."""
        self.sit_quiz()
        self.set_availability(days=2, hours=4)
        self.press_standing_button()
        html = self.plan_page()
        self.assertIn("Read Eigenvalues", html)
        self.assertNotIn("Finished it", self.standing(html))


class AWeekBuiltRoundARecordedTopicTests(UnfinishTestCase):
    """Where the strand actually happens: the quiz first, the week after."""

    def setUp(self) -> None:
        super().setUp()
        self.sit_quiz()
        self.set_availability(days=2, hours=4)

    def test_the_strand_is_real_and_the_plan_never_held_the_topic(self) -> None:
        html = self.plan_page()
        self.assertIn("finished", self.standing(html))
        self.assertNotIn("Read Eigenvalues", html)
        self.assertNotIn("not finished", self.standing(html))

    def test_taking_the_topic_back_out_puts_a_session_on_the_plan(self) -> None:
        self.press_standing_button()
        html = self.plan_page()
        self.assertIn("Read Eigenvalues", html)
        self.assertIn("not finished", self.standing(html))

    def test_the_session_log_says_nothing_about_it(self) -> None:
        self.press_standing_button()
        html = self.plan_page()
        log = flat(html.split('class="panel session-log"')[-1].split("</section>")[0])
        self.assertIn("log-empty", log)
        self.assertNotIn("log-topic", log)

    def test_the_sittings_survive_the_toggle(self) -> None:
        self.press_standing_button()
        self.assertIn("from 1 sitting", self.quiz_page())
