"""The mock exam: one paper across the whole course, sat once, and marked as a
whole.

Every test goes in through HTTP and fakes only ``material.model.complete``, the
one place Mastory leaves the machine, so writing the paper, grounding each
question, verifying it and marking it are all the real application. The three
tests that matter most are the two refusals - a second sitting, and a second
paper - and the one that reads the paper before it is handed in: a mock exam
that leaks a mark, a citation or an expected answer while the student is still
sitting is not an exam, and that can only be seen from the page.
"""

import re
from typing import Any

from django.test import TestCase

from material import progress
from material.models import MockExam, MockQuestion, Topic
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

TOPICS = (
    ("Eigenvalues", [2, 3]),
    ("Eigenspaces", [4]),
)

#: A question and an answer the fixture's paragraphs really contain, so the fake
#: has something true to write about whichever topic it is asked for.
FROM_THE_MATERIAL = fake_question(
    1, "What is the vector v called in Av = lambda v?", answer="eigenvector"
)

#: What the exam must never say before the paper is handed in. Every one of these
#: is something the topic quiz shows while the student answers, and every one of
#: them is a way of telling a student something about their own paper.
BEFORE_THE_PAPER = (
    "data-correct",
    ">Right<",
    "Not this one",
    "What the paragraph says",
    "You got",
    "read that paragraph again",
)


def flat(text: str) -> str:
    """The page with its line wrapping squeezed out."""
    return " ".join(text.split())


class OnlyTheFirstTopic(QuizModel):
    """A fake that can write nothing verifiable from the second topic onwards.

    Stands in for a course where one topic's material has nothing in it worth
    asking about, which is the case a paper covering "every topic" can silently
    fail at.
    """

    def __init__(self, *questions: dict[str, object]) -> None:
        super().__init__(*questions)
        self.writes = 0

    def _write(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        self.writes += 1
        if self.writes > 1:
            # An answer the paragraph never uses, which the application drops
            # before it is ever verified.
            return [
                dict(
                    fake_question(
                        span["span"],
                        "What is the spin of a neutron?",
                        kind="short_answer",
                        answers=["quantum tunnelling"],
                    )
                )
                for span in request["spans"]
            ]
        return super()._write(request)


class ExamTestCase(TestCase):
    """One course uploaded, its topic path confirmed, and no exam written yet."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        claims = [fake_topic(title, slides) for title, slides in TOPICS]
        with use_fake_model(*claims):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/confirm/")

    # -- reaching the exam the way a student reaches it ------------------------

    def page(self) -> str:
        response = self.client.get("/courses/1/exam/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def start(self, model: QuizModel) -> str:
        """Ask for the paper, with this fake model writing it."""
        model.use(self)
        response = self.client.post("/courses/1/exam/start/", follow=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def paper(self, model: QuizModel | None = None) -> str:
        """The paper waiting to be sat, written on first ask and never rewritten."""
        if not MockExam.objects.exists():
            self.start(model or QuizModel(FROM_THE_MATERIAL))
        html = self.page()
        self.assertIn("Hand in my exam", flat(html), "the exam page shows no paper")
        return html

    def remove_every_topic(self) -> None:
        """A course whose topic path is empty, which is what a student is left with
        after deleting both topics."""
        html = self.client.get("/courses/1/topics/").content.decode()
        for title, _ in TOPICS:
            self.client.post(f"/courses/1/topics/{topic_pk(html, title)}/remove/")

    def question_ids(self, html: str) -> list[str]:
        return re.findall(r'id="question-(\d+)"', html)

    def blocks(self, html: str) -> list[str]:
        return html.split('<li class="question')[1:]

    def options(self, html: str, question: str) -> list[str]:
        """The choices a question offers, in the order the page shows them."""
        block = next(b for b in self.blocks(html) if f'id="question-{question}"' in b)
        return re.findall(r'value="([^"]*)"', block)

    def right(self, html: str) -> dict[str, str]:
        """The right answer to every question on the paper.

        Taken as the first choice, because that is where the fake model writes the
        option the paragraph contains; a test marking a whole paper right should
        not have to know which question it is looking at.
        """
        return {
            question: self.options(html, question)[0]
            for question in self.question_ids(html)
        }

    def hand_in(self, html: str, answers: dict[str, str]) -> str:
        """Hand the paper in the way the form does: one field per question.

        The url and the exam the form was drawn from are read off the page rather
        than built, so the paper is marked as a browser would send it.
        """
        action = re.search(r'action="([^"]+)"', flat(html))
        assert action is not None, "the exam page offers no form to hand in"
        which = re.search(r'name="exam" value="(\d+)"', html)
        assert which is not None, "the form does not say which exam it is for"
        return self.post_to(action.group(1), which.group(1), answers)

    def post_to(self, action: str, which: str, answers: dict[str, str]) -> str:
        """The same request again, which is how a refusal is provoked."""
        response = self.client.post(
            action,
            {
                "nonsense": "",
                "exam": which,
                **{f"q-{key}": value for key, value in answers.items()},
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def form(self, html: str) -> tuple[str, str]:
        """The url and the exam id the page's own form would send."""
        action = re.search(r'action="([^"]+)"', flat(html))
        which = re.search(r'name="exam" value="(\d+)"', html)
        assert action is not None and which is not None
        return action.group(1), which.group(1)


class AskedForTests(ExamTestCase):
    def test_nothing_is_written_until_the_student_asks_for_it(self) -> None:
        html = self.page()
        self.assertEqual(MockExam.objects.count(), 0)
        self.assertIn("Nothing has been written yet", flat(html))
        self.assertNotIn("Hand in my exam", flat(html))

    def test_asking_for_one_writes_the_paper(self) -> None:
        self.paper()
        self.assertEqual(MockExam.objects.count(), 1)
        self.assertTrue(MockQuestion.objects.exists())

    def test_the_page_says_the_paper_is_one_attempt_and_is_never_rewritten(self) -> None:
        page = flat(self.page())
        self.assertIn("one attempt", page)
        self.assertIn("are not written again", page)

    def test_a_course_with_no_topics_left_says_so_and_writes_nothing(self) -> None:
        """An exam across nothing is not an exam, and a student whose topics are
        all gone must not be handed one attempt at an empty paper."""
        self.remove_every_topic()
        self.start(QuizModel(FROM_THE_MATERIAL))
        self.assertEqual(MockExam.objects.count(), 0)

    def test_a_course_with_no_topics_left_says_why(self) -> None:
        self.remove_every_topic()
        html = self.start(QuizModel(FROM_THE_MATERIAL))
        self.assertIn("point at paragraphs to ask about", flat(html))


class WholeCourseTests(ExamTestCase):
    def test_the_paper_asks_about_every_topic_in_the_course(self) -> None:
        html = self.paper()
        on_it = set(MockQuestion.objects.values_list("topic__title", flat=True))
        self.assertEqual(on_it, {"Eigenvalues", "Eigenspaces"})
        self.assertIn("covers the whole course", flat(html))

    def test_each_topic_gets_its_own_questions(self) -> None:
        self.paper()
        for title, _ in TOPICS:
            with self.subTest(topic=title):
                self.assertTrue(
                    MockQuestion.objects.filter(topic__title=title).exists()
                )

    def test_the_page_says_how_big_the_paper_is(self) -> None:
        html = flat(self.paper())
        asked = MockQuestion.objects.count()
        self.assertIn(
            f"{asked} questions from 2 of the course&rsquo;s 2 topics, "
            "so it covers the whole course",
            html,
        )

    def test_a_topic_nothing_could_be_written_from_is_named_rather_than_hidden(self) -> None:
        """A topic whose paragraphs support no verified question leaves a hole in
        the paper, and the page names that topic rather than only showing a number
        lower than it should be."""
        html = self.paper(OnlyTheFirstTopic(FROM_THE_MATERIAL))
        page = flat(html)
        self.assertIn("which is not the whole course", page)
        self.assertIn("Eigenspaces", page)
        self.assertEqual(
            set(MockQuestion.objects.values_list("topic__title", flat=True)),
            {"Eigenvalues"},
        )

    def test_what_was_left_out_of_the_paper_is_said(self) -> None:
        html = flat(self.paper())
        dropped = MockExam.objects.get().dropped
        self.assertGreater(dropped, 0)
        self.assertIn(f"{dropped} questions more were written and not shown", html)


class BeforeSubmissionTests(ExamTestCase):
    def test_the_paper_says_nothing_about_the_answers(self) -> None:
        html = self.paper()
        for phrase in BEFORE_THE_PAPER:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, html)

    def test_the_paper_does_not_name_the_paragraph_each_question_came_from(self) -> None:
        """A citation before the answer is reading ahead to it: it says the answer
        is in that paragraph, which is the one thing an exam may not do while the
        student is sitting it."""
        html = flat(self.paper())
        self.assertNotIn("From math-201-eigenvalues.pdf", html)
        self.assertNotRegex(html, r"page \d+, paragraph \d+")

    def test_the_paper_does_not_link_into_the_paragraphs_it_came_from(self) -> None:
        self.assertNotIn("/courses/1/read/#", self.paper())

    def test_the_paper_is_a_question_per_topic_and_nothing_else(self) -> None:
        html = self.paper()
        self.assertEqual(len(self.blocks(html)), MockQuestion.objects.count())
        self.assertEqual(len(self.blocks(html)), 4)
        for block in self.blocks(html):
            question = re.search(r'id="question-(\d+)"', block)
            assert question is not None, f"a question with no id: {block}"
            with self.subTest(question=question.group(1)):
                self.assertIn('name="q-', block)

    def test_the_paper_says_no_score_while_it_is_being_sat(self) -> None:
        html = self.paper()
        self.assertNotIn("meter-fill", html)
        self.assertNotIn("You got", flat(html))


class ScoringTests(ExamTestCase):
    def test_the_paper_is_marked_as_a_whole(self) -> None:
        """One mark, one share, across every question - not a row of per-topic
        marks that each say something flattering about one topic."""
        html = self.paper()
        right = self.right(html)
        asked = len(right)
        first = next(iter(right))
        wrong = {q: "wrong" for q in right if q != first}
        marked = self.hand_in(html, {first: right[first], **wrong})
        self.assertIn(f"You got 1 of {asked} right", flat(marked))

    def test_the_meter_shows_the_score_of_the_whole_paper(self) -> None:
        marked = self.hand_in(self.paper(), self.right(self.paper()))
        width = re.search(r'class="meter-fill" style="width: (\d+)%"', marked)
        assert width is not None, "no meter on the marked exam"
        self.assertEqual(width.group(1), "100")

    def test_the_score_is_recorded_on_the_exam(self) -> None:
        self.hand_in(self.paper(), self.right(self.paper()))
        exam = MockExam.objects.get()
        self.assertEqual(exam.score, 1.0)
        self.assertEqual(exam.passes, exam.questions.count())
        self.assertTrue(exam.sat)

    def test_a_blank_paper_is_a_score_of_nothing_and_not_a_missing_sitting(self) -> None:
        marked = self.hand_in(self.paper(), {})
        asked = MockQuestion.objects.count()
        self.assertIn(f"You got 0 of {asked} right", flat(marked))
        self.assertIn("You left this blank", marked)

    def test_the_answers_are_kept_so_the_paper_can_be_read_back(self) -> None:
        self.hand_in(self.paper(), self.right(self.paper()))
        for question in MockQuestion.objects.all():
            with self.subTest(ordinal=question.ordinal):
                self.assertTrue(question.given)

    def test_the_exam_does_not_finish_or_weigh_a_topic(self) -> None:
        """Mastery and completion are read from a topic's own sittings. An exam is
        one sample of the whole course at the end of it, and letting its two
        questions a topic outweigh four sittings would make the number describe
        the exam rather than what the student knows."""
        self.hand_in(self.paper(), self.right(self.paper()))
        for title, _ in TOPICS:
            with self.subTest(topic=title):
                topic = Topic.objects.get(title=title)
                self.assertEqual(progress.attempts(topic), [])
                self.assertIsNone(topic.completed_on)

    def test_the_mark_is_the_same_one_a_topic_quiz_would_give(self) -> None:
        """Both kinds of paper are marked by one function, so the same sentence is
        worth the same mark on either."""
        html = self.paper()
        question = self.question_ids(html)[0]
        marked = flat(self.hand_in(html, {question: self.options(html, question)[0]}))
        self.assertIn("Right", marked)


class OneAttemptTests(ExamTestCase):
    def test_a_second_hand_in_is_refused(self) -> None:
        html = self.paper()
        self.hand_in(html, self.right(html))
        action, which = self.form(html)
        again = flat(
            self.post_to(action, which, {q: "wrong" for q in self.question_ids(html)})
        )
        self.assertIn("one attempt your mock exam gets", again)
        # The standing score is the first sitting's, not the second's.
        self.assertIn("You got 4 of 4 right", again)
        self.assertEqual(MockExam.objects.get().score, 1.0)

    def test_a_second_hand_in_leaves_the_answers_the_student_gave(self) -> None:
        """Refusing a second sitting has to mean the paper keeps what was written
        on it; a refusal that still overwrote the answers would have destroyed the
        one sitting for nothing."""
        html = self.paper()
        self.hand_in(html, self.right(html))
        action, which = self.form(html)
        self.post_to(action, which, {q: "wrong" for q in self.question_ids(html)})
        self.assertTrue(all(q.given for q in MockQuestion.objects.all()))

    def test_asking_for_another_exam_after_sitting_one_is_refused(self) -> None:
        """The questions were written once and are not written again, so a second
        request cannot quietly replace the paper a student is holding."""
        html = self.paper()
        asked = self.question_ids(html)
        self.hand_in(html, self.right(html))
        page = flat(self.client.post("/courses/1/exam/start/", follow=True).content.decode())
        self.assertIn("one attempt your mock exam gets", page)
        self.assertEqual(MockExam.objects.count(), 1)
        self.assertEqual(self.question_ids(page), asked)

    def test_coming_back_before_sitting_shows_the_same_paper(self) -> None:
        """The student has to be able to come back to the questions they read,
        because the answers they mark are marked against those."""
        html = self.paper()
        self.assertEqual(self.question_ids(html), self.question_ids(self.page()))

    def test_coming_back_after_sitting_shows_the_result_not_the_paper(self) -> None:
        html = self.paper()
        self.hand_in(html, {})
        asked = MockQuestion.objects.count()
        again = self.page()
        self.assertIn(f"You got 0 of {asked} right", flat(again))
        self.assertNotIn("Hand in my exam", flat(again))

    def test_a_form_naming_another_exam_is_not_marked(self) -> None:
        html = self.paper()
        page = flat(
            self.client.post(
                "/courses/1/exam/submit/",
                {"exam": "999", **{f"q-{q}": "wrong" for q in self.question_ids(html)}},
                follow=True,
            ).content.decode()
        )
        self.assertIn("not the mock exam of this course", page)
        self.assertNotIn("You got", page)
        self.assertFalse(MockExam.objects.get().sat)


class CitationJumpTests(ExamTestCase):
    def test_a_wrong_answer_sends_the_student_to_the_paragraph_it_came_from(self) -> None:
        html = self.paper()
        marked = self.hand_in(html, {q: "wrong" for q in self.question_ids(html)})
        cited = set(re.findall(r'href="/courses/1/read/#([^"]+)"', flat(marked)))
        self.assertTrue(cited)
        reading = self.client.get("/courses/1/read/").content.decode()
        for anchor in cited:
            with self.subTest(anchor=anchor):
                self.assertIn(f'id="{anchor}"', reading)

    def test_every_wrong_answer_is_pushed_at_and_the_right_ones_are_not(self) -> None:
        html = self.paper()
        right = self.right(html)
        first = next(iter(right))
        marked = self.hand_in(
            html, {first: right[first], **{q: "wrong" for q in right if q != first}}
        )
        self.assertEqual(marked.count("Not this one"), len(right) - 1)
        self.assertEqual(marked.count("read that paragraph again"), len(right) - 1)
        self.assertEqual(marked.count("open that paragraph"), 1)

    def test_the_citation_and_the_anchor_name_the_same_paragraph(self) -> None:
        """The two halves of a citation have to agree: a student who reads
        "paragraph 3" and lands on paragraph 2 has been sent to check the wrong
        thing, which is worse than a citation that pointed nowhere."""
        html = self.paper()
        marked = self.hand_in(html, {q: "wrong" for q in self.question_ids(html)})
        for block in self.blocks(marked):
            cited = re.search(r"page (\d+), paragraph (\d+)", block)
            anchor = re.search(r'href="/courses/1/read/#\d+-p(\d+)-s(\d+)"', block)
            assert cited and anchor, f"a question cites nothing: {block}"
            with self.subTest(cited=cited.groups()):
                self.assertEqual(cited.group(1), anchor.group(1))
                self.assertEqual(int(cited.group(2)), int(anchor.group(2)) + 1)

    def test_the_marked_paper_names_the_topic_each_question_came_from(self) -> None:
        """Afterwards the paper can be read against the syllabus, which is what
        makes a score of 40% an answer rather than a number."""
        html = self.paper()
        marked = flat(self.hand_in(html, {q: "wrong" for q in self.question_ids(html)}))
        for title, _ in TOPICS:
            with self.subTest(topic=title):
                self.assertIn(title, marked)


class TopicQuizIsNotSpentTests(ExamTestCase):
    def test_sitting_the_exam_takes_nothing_off_a_topic_quiz(self) -> None:
        """The two kinds of paper are separate things: the exam costs one attempt
        and the topic quiz costs nothing, so a student who sits the exam can go
        back to the topic quizzes as often as they like."""
        html = self.paper()
        self.hand_in(html, self.right(html))
        topics = self.client.get("/courses/1/topics/").content.decode()
        quiz = self.client.get(
            f"/courses/1/topics/{topic_pk(topics, 'Eigenvalues')}/quiz/"
        )
        self.assertEqual(quiz.status_code, 200)
        self.assertIn("Write different questions", quiz.content.decode())