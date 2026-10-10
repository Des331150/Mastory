"""The retry policy: retaking is free, and the next attempt waits until the
student has been back to the paragraphs their wrong answers came from.

This goes in through HTTP like everything else. The student reads a quiz, marks
it wrong, is held, opens the paragraph they were sent to, and is let through on
the very next press. The fake model is still the only thing standing in for
anything outside the machine, and it is installed at the same one boundary.

Two rules are being defended at once, and the tests here exist to stop either
one eating the other. Retaking is unlimited and costs nothing, so nothing here
can make a student lose the ability to sit a quiz. And after two failed sittings
the next one waits for the material, in the server, where dismissing anything
the page offers changes nothing - which is the only version of this feature that
is not decoration.
"""

import re
from typing import Any

from django.test import TestCase

from material import progress
from material.models import Topic
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

TOPICS = (("Eigenvalues", [2, 3]),)

#: A question and an answer the fixture's paragraphs really contain, so the fake
#: model has something true to write about.
FROM_THE_MATERIAL = fake_question(
    1, "What is the vector v called in Av = lambda v?", answer="eigenvector"
)

#: The questions a retake is written from. Different enough that a page showing
#: them cannot be the page a student has just memorised.
DIFFERENT_QUESTIONS = fake_question(
    1, "What is the spectrum of A?", answer="the set of its eigenvalues"
)

#: A third set, so a retake can be told apart from the sitting before it even
#: when the model is the same deterministic one.
ANOTHER_SET = fake_question(
    1, "What is the trace of A?", answer="the sum of its eigenvalues"
)


def flat(text: str) -> str:
    """The page with its line wrapping squeezed out."""
    return " ".join(text.split())


class RetryTestCase(TestCase):
    """One course, one confirmed topic, and a quiz the student can keep failing."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        with use_fake_model(*(fake_topic(title, slides) for title, slides in TOPICS)):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/confirm/")

    # -- reading the page as a student reads it ------------------------------

    def topic_id(self) -> str:
        return topic_pk(
            self.client.get("/courses/1/topics/").content.decode(), "Eigenvalues"
        )

    def quiz_url(self) -> str:
        return f"/courses/1/topics/{self.topic_id()}/quiz/"

    def blocks(self, html: str) -> list[str]:
        return html.split('<li class="question')[1:]

    def question_ids(self, html: str) -> list[str]:
        return re.findall(r'id="question-(\d+)"', html)

    def prompts(self, html: str) -> list[str]:
        """What the page actually asks, rather than which rows it happens to be
        holding.

        A quiz written again is compared by what it asks: row ids are reused the
        moment the old quiz is deleted, so two different sets of questions can
        look identical and a retake can be made not to have happened.
        """
        return re.findall(r'class="question-prompt">(.*?)</p>', html)

    def owed(self, html: str) -> list[tuple[str, str]]:
        """What the page is holding the next attempt for.

        Each entry is a paragraph id and the anchor it is named by, read off the
        refusal itself rather than built from an id the test happened to know.
        """
        return re.findall(r'read/\?paragraph=(\d+)#([\w-]+)', flat(html))

    def topic(self) -> Topic:
        return Topic.objects.get(title="Eigenvalues")

    def attempts(self) -> int:
        return len(progress.attempts(self.topic()))

    # -- sitting and taking it again -----------------------------------------

    def quiz(self, question: dict[str, Any] | None = None) -> str:
        """The quiz as it stands, written if the student has not seen one."""
        QuizModel(question or FROM_THE_MATERIAL).use(self)
        return self.page()

    def page(self) -> str:
        response = self.client.get(self.quiz_url())
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def rewrite(self, question: dict[str, Any] | None = None) -> str:
        """Ask for different questions, the way the button on the page does."""
        QuizModel(question or FROM_THE_MATERIAL).use(self)
        response = self.client.post(f"{self.quiz_url()}rewrite/", follow=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def sit(self, *, right: bool = False) -> str:
        """Mark one sitting of the questions the topic currently has.

        The url and the quiz the form was drawn from are read off the page, as a
        browser would send them, so a sitting is marked on the questions the
        student was actually looking at.
        """
        QuizModel(FROM_THE_MATERIAL).use(self)
        html = self.page()
        action = re.search(r'action="([^"]+)"', flat(html))
        assert action is not None, "the quiz page offers no form to mark"
        which = re.search(r'name="quiz" value="(\d+)"', html)
        assert which is not None, "the form does not say which quiz it is for"
        answers = {
            question_id: (re.findall(r'value="([^"]*)"', block)[0] if right else "wrong")
            for block, question_id in zip(self.blocks(html), self.question_ids(html))
        }
        response = self.client.post(
            action.group(1),
            {"quiz": which.group(1), **{f"q-{key}": value for key, value in answers.items()}},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def sit_wrongly(self, times: int = 1, question: dict[str, Any] | None = None) -> str:
        """Sit the quiz wrong ``times`` times, writing it again in between.

        Rewriting in between is what a student does who wants a different set of
        questions, and it is what makes the sittings different events rather
        than one sitting pressed four times.
        """
        html = self.quiz(question)
        for _ in range(times):
            html = self.sit()
            if _ < times - 1:
                html = self.rewrite(DIFFERENT_QUESTIONS)
        return html

    def sit_wrongly_twice(self) -> str:
        """Two sittings the student got wrong, which is what starts the rule."""
        return self.sit_wrongly(times=2)

    # -- going back to the material ------------------------------------------

    def open_paragraph(self, span_id: str) -> None:
        """Open a cited paragraph by its own link, as the refusal offers it."""
        response = self.client.get(f"/courses/1/read/?paragraph={span_id}")
        self.assertEqual(response.status_code, 200)

    def open_all(self, html: str) -> None:
        for span_id, _ in self.owed(html):
            self.open_paragraph(span_id)

    def beacon(self, anchor: str) -> str:
        """The event the reading surface advertises for one paragraph.

        Read off the page rather than built from an id, so a test is asking the
        page what a browser would send rather than telling it.
        """
        reading = self.client.get("/courses/1/read/").content.decode()
        found = re.search(rf'id="{re.escape(anchor)}" hx-post="([^"]+)"', reading)
        assert found is not None, f"{anchor} does not report itself being opened"
        return found.group(1)


class UnlimitedRetakeTests(RetryTestCase):
    def test_a_wrong_sitting_costs_nothing_and_the_next_one_is_free(self) -> None:
        """One wrong sitting is information, not a habit: the student asks for
        different questions and gets them."""
        self.sit()
        again = self.rewrite(DIFFERENT_QUESTIONS)
        self.assertTrue(self.question_ids(again))
        self.assertNotIn("Back to the material first", flat(again))
        self.assertNotIn("waits until you have opened", flat(again))

    def test_a_student_who_got_it_right_can_still_sit_it_again(self) -> None:
        """They are not the student this rule is about, and nothing they have
        done costs them anything."""
        self.sit_wrongly(times=1)
        self.assertIn("You got 4 of 4 right", flat(self.sit(right=True)))
        self.assertNotIn(
            "Back to the material first", flat(self.rewrite(DIFFERENT_QUESTIONS))
        )

    def test_the_questions_are_different_on_a_retake_after_two_failed_attempts(
        self,
    ) -> None:
        first = set(self.prompts(self.sit_wrongly(times=1)))
        second = set(self.prompts(self.sit_wrongly_twice()))
        self.assertTrue(first and second)
        self.assertNotEqual(first & second, set(), "the same quiz was served twice")


class BlockedRetryTests(RetryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.marked = self.sit_wrongly_twice()

    def test_the_next_attempt_is_refused_before_the_section_is_opened(self) -> None:
        refused = flat(self.rewrite(DIFFERENT_QUESTIONS))
        self.assertIn("Back to the material first", refused)
        self.assertNotIn("Write different questions", refused)
        self.assertNotIn("Mark my answers", refused)

    def test_nothing_is_generated_or_recorded_by_a_refused_attempt(self) -> None:
        """A refused attempt costs the student nothing and costs the product a
        model call it did not need to make."""
        before = self.attempts()
        self.rewrite(DIFFERENT_QUESTIONS)
        self.assertEqual(self.attempts(), before)

    def test_sitting_the_same_questions_again_is_refused_too(self) -> None:
        """Otherwise the block is a speed bump: press the other button instead and
        keep answering the same four until one of them sticks, which is the
        thing the rule exists to stop."""
        html = self.page()
        which = re.search(r'name="quiz" value="(\d+)"', html)
        assert which is not None
        response = self.client.post(
            f"{self.quiz_url()}submit/",
            {
                "quiz": which.group(1),
                **{f"q-{q}": "wrong" for q in self.question_ids(html)},
            },
            follow=True,
        )
        page = flat(response.content.decode())
        self.assertEqual(response.status_code, 200)
        self.assertIn("Back to the material first", page)
        self.assertNotIn("You got", page)
        self.assertEqual(self.attempts(), 2)

    def test_the_refusal_says_what_will_unlock_the_retry(self) -> None:
        page = flat(self.rewrite(DIFFERENT_QUESTIONS))
        self.assertIn("waits until you have opened", page)
        self.assertIn("as many times as you like", page)

    def test_the_refusal_names_the_paragraph_the_wrong_answer_came_from(self) -> None:
        page = self.rewrite(DIFFERENT_QUESTIONS)
        self.assertIn("math-201-eigenvalues.pdf", flat(page))
        self.assertIn("paragraph ", flat(page))
        self.assertTrue(self.owed(page))

    def test_nothing_the_student_can_post_unlocks_the_retry(self) -> None:
        """The block is in the server. There is no prompt to dismiss and no field
        that stands in for having read anything."""
        self.client.post(
            f"{self.quiz_url()}rewrite/",
            {"dismiss": "1", "skip": "1", "opened": "1", "confirm": "yes"},
            follow=True,
        )
        still = flat(self.page())
        self.assertIn("Back to the material first", still)
        self.assertTrue(self.owed(still))

    def test_opening_another_paragraph_does_not_unlock_the_retry(self) -> None:
        """It has to be the paragraphs they were sent to. Opening the rest of the
        deck is not a way round them, and a student told which paragraphs those
        are should not be able to satisfy the rule by opening all the others."""
        sent_to = {anchor for _, anchor in self.owed(self.marked)}
        reading = self.client.get("/courses/1/read/").content.decode()
        others = [
            url
            for anchor, url in re.findall(r'id="([\w-]+)" hx-post="([^"]+)"', reading)
            if anchor not in sent_to
        ]
        self.assertTrue(others, "every paragraph on the page is one they were sent to")
        for url in others:
            self.client.post(url)
        self.assertIn("Back to the material first", flat(self.rewrite()))

    def test_opening_the_cited_section_unlocks_the_retry_immediately(self) -> None:
        owed = self.owed(self.marked)
        self.assertTrue(owed, "two failures and nothing is being asked of them")
        for span_id, _ in owed:
            self.open_paragraph(span_id)
        unlocked = self.rewrite(DIFFERENT_QUESTIONS)
        self.assertNotIn("Back to the material first", flat(unlocked))
        self.assertTrue(self.question_ids(unlocked))

    def test_unlocking_is_the_whole_list_and_not_the_first_one(self) -> None:
        owed = self.owed(self.marked)
        for span_id, _ in owed[:-1]:
            self.open_paragraph(span_id)
        still = flat(self.rewrite(DIFFERENT_QUESTIONS))
        self.assertIn("Back to the material first", still)
        self.open_paragraph(owed[-1][0])
        gone = self.rewrite(DIFFERENT_QUESTIONS)
        self.assertNotIn("Back to the material first", flat(gone))

    def test_the_third_attempt_is_different_questions_once_it_is_unlocked(self) -> None:
        """Written again, not handed back. A student held for not having read the
        material and then let through gets new questions rather than the ones
        they were held over."""
        second = set(self.prompts(self.marked))
        self.open_all(self.marked)
        third = self.rewrite(ANOTHER_SET)
        self.assertNotEqual(second, set(self.prompts(third)))
        self.assertIn("What is the trace of A?", flat(third))

    def test_a_student_can_keep_sitting_it_after_that_as_many_times_as_they_like(
        self,
    ) -> None:
        self.open_all(self.marked)
        for _ in range(3):
            marked = self.sit()
            self.assertIn("You got", flat(marked))
            self.open_all(marked)
        self.assertEqual(self.attempts(), 5)


class OpenedParagraphTests(RetryTestCase):
    """The open event itself: where it comes from and what it counts as."""

    def setUp(self) -> None:
        super().setUp()
        self.marked = self.sit_wrongly_twice()

    def test_the_reading_surface_reports_a_paragraph_opened_when_it_appears(self) -> None:
        """The page says so itself: every paragraph carries the event that
        reports it, so going back to the material is not something the student
        has to go looking for."""
        for _, anchor in self.owed(self.marked):
            self.assertEqual(self.client.post(self.beacon(anchor)).status_code, 204)
        unlocked = self.rewrite(DIFFERENT_QUESTIONS)
        self.assertNotIn("Back to the material first", flat(unlocked))

    def test_every_paragraph_on_the_page_reports_itself(self) -> None:
        reading = self.client.get("/courses/1/read/").content.decode()
        reported = re.findall(r'id="([\w-]+)" hx-post="[^"]+" hx-trigger="revealed"', reading)
        self.assertTrue(reported)
        self.assertIn(reported[0], re.findall(r'id="([\w-]+)"', reading))

    def test_opening_the_reading_page_alone_records_nothing(self) -> None:
        """Opening a section is an event with a paragraph in it. Loading the
        material is not the same as having gone to the paragraph the student was
        sent to, and the page is what says which one they were sent to."""
        self.client.get("/courses/1/read/")
        self.assertIn("Back to the material first", flat(self.rewrite()))

    def test_an_event_for_another_course_s_paragraph_is_refused(self) -> None:
        """Scoped through the course, so an id from a course the student is not
        in cannot be recorded as an opening against this one."""
        self.client.post(
            "/courses/new/",
            {"title": "Other", "files": [pdf_upload(LINEAR_ALGEBRA_PDF)]},
        )
        response = self.client.post("/courses/2/read/paragraphs/1/open/")
        self.assertEqual(response.status_code, 404)