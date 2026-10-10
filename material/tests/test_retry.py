"""The retry policy: retaking is free, and the next attempt waits until the
student has been back to the paragraphs their wrong answers came from.

This goes in through HTTP like everything else. The student reads a quiz, marks
it wrong, is held, opens a paragraph they were sent to, and is let through on
the very next press. The fake model is still the only thing standing in for
anything outside the machine, and it is installed at the same one boundary.

Two rules are being defended at once, and the tests here exist to stop either
one eating the other. Retaking is unlimited and costs nothing, so nothing here
can make a student lose the ability to sit a quiz. And after two failed sittings
the next one waits for the material, in the server, where dismissing anything
the page offers changes nothing - which is the only version of this feature that
is not decoration.

One test here does not trust the usual test client: ``Client(enforce_csrf_checks
=True)`` behaves as a browser does, because the event the reading surface fires
is a POST and Django refuses a POST without a token. A suite that only ever posts
through the lenient client would happily assert on a 403 the student never sees.
"""

import re
from typing import Any

from django.test import Client, TestCase

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
        """What the page is holding the next attempt for: a paragraph id and the
        anchor it would send the student to, read off the refusal itself rather
        than built from an id the test happened to know.
        """
        return [(span_id, back.rsplit("#", 1)[-1]) for _, span_id, back in self.buttons(html)]

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
        student was actually looking at. The page the form came from is kept as
        ``sat``, because once the answer is in, the marked page no longer carries
        the form and a test checking what would have been sent needs it.
        """
        QuizModel(FROM_THE_MATERIAL).use(self)
        html = self.page()
        self.sat = html
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
        than one sitting pressed twice.
        """
        html = self.quiz(question)
        for done in range(times):
            html = self.sit()
            if done < times - 1:
                html = self.rewrite(DIFFERENT_QUESTIONS)
        return html

    def sit_wrongly_twice(self) -> str:
        """Two sittings the student got wrong, which is what starts the rule.

        Leaves them held: the third attempt is the one waiting, and a test that
        wanted it free would have to open something first.
        """
        return self.sit_wrongly(times=2)

    # -- going back to the material ------------------------------------------

    def open_all(self, html: str) -> None:
        """Open every paragraph the page is holding the next attempt for.

        Presses the buttons the page offers, at the urls it offers them, so the
        recording is the one a student pressing them would produce.
        """
        for action, span_id, back in self.buttons(html):
            with self.subTest(span=span_id):
                response = self.client.post(action, {"next": back}, follow=True)
                self.assertEqual(response.status_code, 200)

    def buttons(self, html: str) -> list[tuple[str, str, str]]:
        """Every "open this paragraph" the page offers: where it posts, which
        paragraph it is about, and where it sends the student afterwards.

        Read form by form rather than by one pattern, because a form also carries
        a token between its action and the field this is looking for.
        """
        found: list[tuple[str, str, str]] = []
        for form in flat(html).split("<form ")[1:]:
            action = re.search(r'action="([^"]*/paragraphs/(\d+)/open/)"', form)
            back = re.search(r'name="next" value="([^"]+)"', form)
            if action and back:
                found.append((action.group(1), action.group(2), back.group(1)))
        return found

    def beacon(self, anchor: str) -> str:
        """The event the reading surface advertises for one paragraph.

        Read off the page rather than built from an id, so a test is asking the
        page what a browser would send rather than telling it.
        """
        reading = self.client.get("/courses/1/read/").content.decode()
        found = re.search(rf'id="{re.escape(anchor)}" hx-post="([^"]+)"', reading)
        assert found is not None, f"{anchor} does not report itself being opened"
        return found.group(1)

    def scroll_to(self, anchor: str) -> int:
        """Post the paragraph's event the way htmx does: as itself, not as a form.

        htmx marks its own requests, and the view answers those with an empty
        204 rather than a redirect. Posting without that header tests a
        different door - the button - and a test that claims to be the reading
        surface should not quietly be the other one.
        """
        return self.client.post(
            self.beacon(anchor), HTTP_HX_REQUEST="true"
        ).status_code


class UnlimitedRetakeTests(RetryTestCase):
    def test_a_wrong_sitting_costs_nothing_and_the_next_one_is_free(self) -> None:
        """One wrong sitting is information, not a habit: the student asks for
        different questions and gets them."""
        self.sit()
        again = self.rewrite(DIFFERENT_QUESTIONS)
        self.assertTrue(self.question_ids(again))
        self.assertNotIn("Back to the material first", flat(again))

    def test_a_student_who_got_it_right_can_still_sit_it_again(self) -> None:
        """They are not the student this rule is about, and nothing they have
        done costs them anything."""
        self.sit_wrongly(times=1)
        self.rewrite(DIFFERENT_QUESTIONS)
        self.assertIn("You got 4 of 4 right", flat(self.sit(right=True)))
        self.rewrite(ANOTHER_SET)
        self.assertNotIn("Back to the material first", flat(self.page()))

    def test_the_questions_are_different_on_a_retake_after_two_failed_attempts(
        self,
    ) -> None:
        """The criterion the whole ticket is named for: two failures, then a
        retake that is not the quiz they were just marked on."""
        first = set(self.prompts(self.sit_wrongly(times=1)))
        marked = self.sit_wrongly_twice()
        self.assertTrue(self.buttons(marked), "nothing is being held, so nothing to retake")
        self.open_all(marked)
        third = set(self.prompts(self.rewrite(DIFFERENT_QUESTIONS)))
        self.assertTrue(third)
        self.assertEqual(
            first & third,
            set(),
            "the retake served the questions of the sitting before it",
        )


class BlockedRetryTests(RetryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.marked = self.sit_wrongly_twice()

    def test_the_next_attempt_is_refused_before_the_section_is_opened(self) -> None:
        refused = flat(self.rewrite(DIFFERENT_QUESTIONS))
        self.assertIn("Back to the material first", refused)
        self.assertNotIn("Write different questions", refused)
        self.assertNotIn("Mark my answers", refused)

    def test_a_refused_attempt_makes_no_model_call_and_records_nothing(self) -> None:
        """A held attempt costs the student nothing and costs the product a
        generation it did not need to make."""
        fake = QuizModel(ANOTHER_SET)
        fake.use(self)
        before = self.attempts()
        self.rewrite(DIFFERENT_QUESTIONS)
        self.assertEqual(self.attempts(), before)
        self.assertEqual(fake.prompts, [], "the held retake still called the model")

    def test_sitting_the_same_questions_again_is_refused_too(self) -> None:
        """Otherwise the block is a speed bump: press the other button instead and
        keep answering the same four until one of them sticks, which is the
        thing the rule exists to stop. The form replayed here is the one the
        sitting that caused the hold was marked from."""
        which = re.search(r'name="quiz" value="(\d+)"', self.sat)
        assert which is not None
        response = self.client.post(
            f"{self.quiz_url()}submit/",
            {
                "quiz": which.group(1),
                **{f"q-{q}": "wrong" for q in self.question_ids(self.sat)},
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
        self.assertIn("waits until you have been back to", page)
        self.assertIn("as many times as you like", page)

    def test_the_refusal_counts_the_sittings_and_the_failures_separately(self) -> None:
        """A student who failed, passed, then failed again has sat it three
        times, and being told they sat it two is the page arguing with its own
        database."""
        self.assertIn(
            "You have sat this quiz 2 times and not got past 50% in 2 of them",
            flat(self.rewrite()),
        )

    def test_the_refusal_names_the_paragraphs_the_wrong_answers_came_from(self) -> None:
        """Exactly the paragraphs the marked sitting sent the student to, each
        with the anchor it was cited at - not a list of something else, and not
        a page that cannot be opened."""
        cited = set(re.findall(r'read/#([\w-]+)', flat(self.marked)))
        self.assertTrue(cited, "the marked sitting names no paragraph to go back to")
        owed = self.owed(self.rewrite(DIFFERENT_QUESTIONS))
        self.assertTrue(owed)
        self.assertEqual(
            {anchor for _, anchor in owed}, cited, "the refusal names other paragraphs"
        )

    def test_nothing_the_student_can_post_unlocks_the_retry(self) -> None:
        """The block is in the server. There is no prompt to dismiss and no field
        that stands in for having read anything."""
        self.client.post(
            f"{self.quiz_url()}rewrite/", {"dismiss": "1"}, follow=True
        )
        still = flat(self.page())
        self.assertIn("Back to the material first", still)
        self.assertTrue(self.buttons(still))

    def test_opening_another_paragraph_does_not_unlock_the_retry(self) -> None:
        """It has to be the paragraphs they were sent to. Opening the rest of the
        deck is not a way round them, and a student told which paragraphs those
        are should not be able to satisfy the rule by opening all the others."""
        sent_to = {back.rsplit("#", 1)[-1] for _, _, back in self.buttons(self.marked)}
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

    def test_reading_the_topic_before_failing_twice_does_not_unlock_the_retry(
        self,
    ) -> None:
        """Reading the topic is what this product asks a student to do before
        sitting the quiz, so an all-time record of what they have read would
        discharge the hold before it ever applied. The rule is about being sent
        back, and only being there afterwards counts.
        """
        reading = self.client.get("/courses/1/read/").content.decode()
        beacons = re.findall(r'hx-post="([^"]+)"', reading)
        self.assertTrue(beacons)
        for url in beacons:
            self.client.post(url)
        self.sit_wrongly_twice()
        refused = flat(self.rewrite(DIFFERENT_QUESTIONS))
        self.assertIn("Back to the material first", refused)
        self.assertTrue(self.buttons(refused))

    def test_opening_the_cited_section_unlocks_the_retry_immediately(self) -> None:
        owed = self.buttons(self.marked)
        self.assertTrue(owed, "two failures and nothing is being asked of them")
        self.open_all(self.marked)
        unlocked = self.rewrite(DIFFERENT_QUESTIONS)
        self.assertNotIn("Back to the material first", flat(unlocked))
        self.assertTrue(self.question_ids(unlocked))

    def test_unlocking_is_the_whole_list_and_not_the_first_one(self) -> None:
        owed = self.buttons(self.marked)
        self.assertGreater(len(owed), 1, "the list is too short to prove anything")
        for action, _, back in owed[:-1]:
            self.client.post(action, {"next": back})
        still = flat(self.rewrite(DIFFERENT_QUESTIONS))
        self.assertIn("Back to the material first", still)
        self.open_all(self.marked)
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

    def test_a_student_keeps_going_through_several_blocks(self) -> None:
        """Not one unlock and then a free ride. Each time they fail, the
        paragraphs they were sent to are owed again, because last time they were
        there was before this failure and not after it - so the rule keeps
        applying for as long as they keep getting it wrong, and never costs them
        the ability to sit it.
        """
        self.open_all(self.marked)
        self.rewrite(ANOTHER_SET)
        for round_ in range(3):
            with self.subTest(round=round_):
                marked = self.sit()
                self.assertIn("Back to the material first", flat(marked))
                self.assertTrue(self.buttons(marked))
                self.open_all(marked)
                again = self.rewrite(ANOTHER_SET)
                self.assertNotIn("Back to the material first", flat(again))

    def test_a_spent_quiz_cannot_be_sat_again_even_once_the_retry_is_unlocked(
        self,
    ) -> None:
        """The memorisation route the block would otherwise leave open: unlock,
        come back, and answer the same four questions with the answers still on
        the page they were just marked on. A quiz is written once and sat once;
        the next attempt is a new set and retaking is free.
        """
        self.open_all(self.marked)
        page = self.page()
        self.assertNotIn("Mark my answers", flat(page))
        self.assertIn("Write different questions", flat(page))

        which = re.search(r'name="quiz" value="(\d+)"', self.sat)
        assert which is not None, "the last sitting did not draw a form"
        before = self.attempts()
        response = self.client.post(
            f"{self.quiz_url()}submit/",
            {
                "quiz": which.group(1),
                **{f"q-{q}": "wrong" for q in self.question_ids(self.sat)},
            },
            follow=True,
        )
        refused = flat(response.content.decode())
        self.assertEqual(response.status_code, 200)
        self.assertIn("You have sat these questions already", refused)
        self.assertNotIn("You got", refused)
        self.assertEqual(self.attempts(), before, "a spent quiz recorded another sitting")


class OpenedParagraphTests(RetryTestCase):
    """The open event itself: where it comes from, and what it counts as."""

    def test_the_reading_surface_reports_a_paragraph_opened_when_it_appears(self) -> None:
        """The page says so itself: every paragraph carries the event that
        reports it, so going back to the material is not something the student
        has to go looking for."""
        marked = self.sit_wrongly_twice()
        for _, _, back in self.buttons(marked):
            self.assertEqual(self.scroll_to(back.rsplit("#", 1)[-1]), 204)
        unlocked = self.rewrite(DIFFERENT_QUESTIONS)
        self.assertNotIn("Back to the material first", flat(unlocked))

    def test_every_paragraph_on_the_page_reports_itself_and_only_itself(self) -> None:
        """One event per paragraph, each naming that paragraph. A page where every
        paragraph fires the same url records the same paragraph twenty times and
        the student who scrolled to the one they were sent to is still held."""
        reading = self.client.get("/courses/1/read/").content.decode()
        spans = set(re.findall(r'<div class="span" id="([\w-]+)"', reading))
        reported = dict(
            re.findall(
                r'<div class="span" id="([\w-]+)" hx-post="([^"]+)" hx-trigger="revealed"',
                reading,
            )
        )
        self.assertTrue(spans)
        self.assertEqual(set(reported), spans, "a paragraph does not report itself")
        self.assertEqual(len(set(reported.values())), len(spans), "two paragraphs share an event")
        for url in reported.values():
            self.assertRegex(url, r"^/courses/1/read/paragraphs/\d+/open/$")

    def test_the_event_a_paragraph_advertises_names_that_paragraph(self) -> None:
        """Reading the url off the page is only a test of the page if the url is
        right: the normalisation in the determinism tests deliberately hides
        which course and which paragraph it names."""
        marked = self.sit_wrongly_twice()
        for _, span_id, back in self.buttons(marked):
            with self.subTest(span=span_id):
                self.assertEqual(
                    self.beacon(back.rsplit("#", 1)[-1]),
                    f"/courses/1/read/paragraphs/{span_id}/open/",
                )

    def test_another_courses_page_advertises_that_courses_events(self) -> None:
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        reading = self.client.get("/courses/2/read/").content.decode()
        self.assertRegex(reading, r'hx-post="/courses/2/read/paragraphs/\d+/open/"')
        self.assertNotRegex(reading, r'hx-post="/courses/1/read/paragraphs/')

    def test_opening_the_reading_page_alone_records_nothing(self) -> None:
        """Opening a section is an event with a paragraph in it. Loading the
        material is not the same as having been sent to the paragraph the
        student got wrong."""
        self.sit_wrongly_twice()
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

    def test_a_get_cannot_record_an_opening(self) -> None:
        """The reading surface does not write on a GET. A page any site can make
        a browser fetch - an image, a preview - would otherwise be able to
        unlock a student's quiz on their behalf."""
        marked = self.sit_wrongly_twice()
        action, _, _ = self.buttons(marked)[0]
        response = self.client.get(action)
        self.assertEqual(response.status_code, 405)
        self.assertIn("Back to the material first", flat(self.rewrite()))


class BrowserFidelityTests(RetryTestCase):
    """The event is a POST, so it needs the token a POST needs.

    The default test client does not enforce CSRF, which is the only reason a
    dead beacon looks alive in a test suite. These use a client that does.
    """

    def strict(self) -> Client:
        client = Client(enforce_csrf_checks=True)
        client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        with use_fake_model(*(fake_topic(title, slides) for title, slides in TOPICS)):
            client.post("/courses/1/topics/infer/")
        client.post("/courses/1/topics/confirm/")
        return client

    def test_the_page_hands_out_the_token_its_own_events_are_posted_with(self) -> None:
        client = self.strict()
        reading = client.get("/courses/1/read/").content.decode()
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', reading)
        self.assertIsNotNone(token, "the page fires events it gives no token for")
        assert token is not None
        beacon = re.search(r'hx-post="([^"]+)"', reading)
        assert beacon is not None
        allowed = client.post(
            beacon.group(1), HTTP_X_CSRFTOKEN=token.group(1), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(allowed.status_code, 204)
        refused = client.post(beacon.group(1), HTTP_HX_REQUEST="true")
        self.assertEqual(refused.status_code, 403, "a post with no token got through")

    def test_the_button_the_refusal_offers_sends_the_student_to_the_paragraph(
        self,
    ) -> None:
        """The no-JavaScript door: a form carrying a token, and a redirect to the
        paragraph they asked for rather than a blank 204 that looks like a broken
        button."""
        client = self.strict()
        html = self.sit_wrongly_twice()
        action, _, back = self.buttons(html)[0]
        page = client.get(self.quiz_url()).content.decode()
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page)
        self.assertIsNotNone(token, "the refusal offers a form with no token in it")
        assert token is not None
        response = client.post(
            action, {"next": back, "csrfmiddlewaretoken": token.group(1)}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], back)
        self.assertEqual(client.post(action).status_code, 403, "a post with no token got through")