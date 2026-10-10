"""The topic quiz: questions written from the student's own paragraphs, and a
wrong answer sent back to the paragraph it came from.

Every test here goes in through HTTP and fakes only ``material.model.complete``,
the one place Mastory leaves the machine, so the generation, the grounding
checks, the verification pass and the grading are all the real application. The
fake reads the request the way a model does and quotes the paragraphs it was
given, which means a test can make the model claim something the paragraph does
not say and watch the application refuse it - which is the whole acceptance
criterion, and the one that cannot be tested by asserting on a function.
"""

import re

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

TOPICS = (
    ("Eigenvalues", [2, 3]),
    ("Eigenspaces", [4]),
)

#: A question and an answer the paragraphs of this fixture really contain, so
#: the fake has something true to write about.
FROM_THE_MATERIAL = fake_question(
    1, "What is the vector v called in Av = lambda v?", answer="eigenvector"
)

#: An answer the fixture never uses. A model that answers a question this way is
#: writing from outside the student's material, and the quiz must not carry it.
FROM_OUTSIDE_THE_MATERIAL = fake_question(
    1,
    "Who first proved the Riemann hypothesis?",
    kind="short_answer",
    answers=["Grigori Perelman"],
)


def flat(text: str) -> str:
    """The page with its line wrapping squeezed out."""
    return " ".join(text.split())


class QuizTestCase(TestCase):
    """One course uploaded, its topic path confirmed, and a topic to quiz."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        claims = [fake_topic(title, slides) for title, slides in TOPICS]
        with use_fake_model(*claims):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/confirm/")

    # -- reaching the quiz the way a student reaches it ----------------------

    def topic_id(self, title: str = "Eigenvalues") -> str:
        return topic_pk(self.client.get("/courses/1/topics/").content.decode(), title)

    def quiz_url(self, title: str = "Eigenvalues") -> str:
        return f"/courses/1/topics/{self.topic_id(title)}/quiz/"

    def quiz(self, model: QuizModel, title: str = "Eigenvalues") -> str:
        """The quiz the student sees, written by this fake model if there is none."""
        model.use(self)
        return self.page(title)

    def rewrite(self, model: QuizModel, title: str = "Eigenvalues") -> str:
        """The quiz the student sees after asking for different questions."""
        model.use(self)
        response = self.client.post(f"{self.quiz_url(title)}rewrite/", follow=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def page(self, title: str = "Eigenvalues") -> str:
        response = self.client.get(self.quiz_url(title))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def question_ids(self, html: str) -> list[str]:
        return re.findall(r'id="question-(\d+)"', html)

    def blocks(self, html: str) -> list[str]:
        return html.split('<li class="question')[1:]

    def options(self, html: str, question: str) -> list[str]:
        """The choices a question offers, in the order the page shows them."""
        block = next(b for b in self.blocks(html) if f'id="question-{question}"' in b)
        return re.findall(r'value="([^"]*)"', block)

    def right(self, html: str) -> dict[str, str]:
        """The right answer to every multiple choice question on the page.

        Taken as the first choice because that is where the fake model writes
        the option the paragraph contains; a test marking a whole sitting right
        should not have to know which question it is looking at.
        """
        return {
            question: self.options(html, question)[0]
            for question in self.question_ids(html)
        }

    def submit(self, html: str, answers: dict[str, str]) -> str:
        """Mark a sitting the way the form does: one field per question.

        The url is read off the page rather than built, so the sitting is marked
        on the topic the student was actually sitting.
        """
        action = re.search(r'action="([^"]+)"', flat(html))
        assert action is not None, "the quiz page offers no form to mark"
        payload = {"nonsense": "", **{f"q-{key}": value for key, value in answers.items()}}
        response = self.client.post(action.group(1), payload, follow=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class GroundingTests(QuizTestCase):
    def test_the_quiz_is_questions_written_from_the_students_own_paragraphs(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        self.assertIn("What is the vector v called in Av = lambda v?", flat(html))
        self.assertEqual(len(self.blocks(html)), 4)
        self.assertIn("survived being checked", flat(html))

    def test_every_question_says_which_slide_and_paragraph_it_came_from(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        for block in self.blocks(html):
            self.assertIn("math-201-eigenvalues.pdf", block)
            self.assertIn("page ", block)
            self.assertIn("paragraph ", block)

    def test_every_question_cites_the_exact_paragraph_it_came_from(self) -> None:
        """The citation is a link into the reading surface, and the reading
        surface has an anchor at that exact paragraph. A citation that named a
        slide but pointed nowhere would let a student check nothing."""
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        cited = set(re.findall(r'href="/courses/1/read/#([^"]+)"', flat(html)))
        self.assertTrue(cited)
        reading = self.client.get("/courses/1/read/").content.decode()
        for anchor in cited:
            with self.subTest(anchor=anchor):
                self.assertIn(f'id="{anchor}"', reading)

    def test_a_question_the_paragraph_does_not_support_is_dropped_and_the_quiz_is_shorter(
        self,
    ) -> None:
        honest = self.quiz(QuizModel(FROM_THE_MATERIAL))
        self.assertEqual(len(self.blocks(honest)), 4)

        dropped = self.rewrite(QuizModel(FROM_THE_MATERIAL, verified=(200,)))
        self.assertEqual(len(self.blocks(dropped)), 0)
        self.assertIn(
            "could not be checked against the paragraph it came from",
            flat(dropped),
        )
        dropped_count = re.search(r"(\d+) question\w* could not be checked", flat(dropped))
        assert dropped_count is not None
        self.assertGreater(int(dropped_count.group(1)), 0)

    def test_the_page_says_the_quiz_is_not_padded_to_a_size(self) -> None:
        self.quiz(QuizModel(FROM_THE_MATERIAL))
        html = self.rewrite(QuizModel(FROM_THE_MATERIAL, verified=(200,)))
        self.assertIn("they were left out rather than shown to you", flat(html))
        self.assertIn("No questions to sit", flat(html))

    def test_a_short_answer_the_paragraph_never_uses_is_dropped(self) -> None:
        """A model answering from outside the student's slides is refused before
        it is even verified, because there is nothing in the citation for the
        answer to be true of."""
        html = self.quiz(QuizModel(FROM_OUTSIDE_THE_MATERIAL))
        self.assertEqual(len(self.blocks(html)), 0)

    def test_the_quiz_is_only_asked_of_paragraphs_in_the_topic(self) -> None:
        model = QuizModel(FROM_THE_MATERIAL)
        html = self.quiz(model, title="Eigenspaces")
        pages = {block.split("page ")[1].split(",")[0] for block in self.blocks(html)}
        self.assertTrue(pages <= {"4"}, f"a question came from a slide outside the topic: {pages}")


class ConfidenceTests(QuizTestCase):
    def test_a_topic_with_one_verifiable_question_is_flagged_low_confidence(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL, verified=(1,)))
        self.assertEqual(len(self.blocks(html)), 1)
        self.assertIn("could be checked against your own slides", flat(html))

    def test_a_normal_length_quiz_is_not_flagged(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        self.assertNotIn("could be checked against your own slides", flat(html))

    def test_the_flag_is_still_on_the_page_after_the_student_is_marked(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL, verified=(1,)))
        marked = self.submit(html, self.right(html))
        self.assertIn("could be checked against your own slides", flat(marked))


class GradingTests(QuizTestCase):
    def test_a_multiple_choice_answer_is_graded_automatically(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        marked = self.submit(html, self.right(html))
        self.assertIn("You got 4 of 4 right", flat(marked))
        self.assertNotIn("Not this one", flat(marked))

    def test_a_wrong_multiple_choice_answer_is_marked_wrong(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        wrong = {q: choices[-1] for q, choices in
                 ((q, self.options(html, q)) for q in self.question_ids(html))}
        marked = self.submit(html, wrong)
        self.assertIn("You got 0 of 4 right", flat(marked))
        self.assertIn("Not this one", flat(marked))

    def test_a_short_answer_is_graded_against_the_paragraph_not_against_the_model(
        self,
    ) -> None:
        """Marking a short answer is a comparison against a phrase the paragraph
        uses, so the sentence that was verified is the sentence that is marked."""
        model = QuizModel(
            fake_question(
                1,
                "What is the eigenspace of an eigenvalue?",
                kind="short_answer",
                answers=["the null space of A - lambda I"],
            )
        )
        html = self.quiz(model, title="Eigenspaces")
        self.assertTrue(self.question_ids(html))
        question = self.question_ids(html)[0]
        marked = self.submit(html, {question: "the null space of A - lambda I"})
        block = next(b for b in self.blocks(marked) if f'id="question-{question}"' in b)
        self.assertIn("Right", flat(block))

    def test_a_short_answer_that_would_contradict_the_span_cannot_be_right(self) -> None:
        """The answer was checked against the paragraph before the question was
        kept, so a marking that disagrees with the paragraph is not reachable."""
        model = QuizModel(
            fake_question(
                1, "What is the eigenspace of an eigenvalue?",
                kind="short_answer", answers=["the null space of A - lambda I"],
            )
        )
        html = self.quiz(model, title="Eigenspaces")
        question = self.question_ids(html)[0]
        marked = self.submit(html, {question: "the set of its eigenvalues"})
        block = next(b for b in self.blocks(marked) if f'id="question-{question}"' in b)
        self.assertIn("Not this one", flat(block))
        self.assertIn("the null space of A - lambda I", flat(block))

    def test_an_answer_left_blank_is_wrong_rather_than_skipped(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        question = self.question_ids(html)[0]
        marked = self.submit(html, {question: ""})
        self.assertIn("You left this blank", flat(marked))
        self.assertIn("You got 0 of 4 right", flat(marked))

    def test_the_sitting_is_recorded_against_the_topic(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        self.submit(html, self.right(html))
        taken = progress.attempts(Topic.objects.get(title="Eigenvalues"))
        self.assertEqual(len(taken), 1)
        self.assertEqual(taken[0].score, 1.0)
        self.assertEqual(taken[0].passes, 4)


class CitationJumpTests(QuizTestCase):
    def test_a_wrong_answer_sends_the_student_to_the_paragraph_it_came_from(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        marked = self.submit(html, {q: "wrong" for q in self.question_ids(html)})
        cited = set(re.findall(r'href="/courses/1/read/#([^"]+)"', flat(marked)))
        reading = self.client.get("/courses/1/read/").content.decode()
        self.assertTrue(cited)
        for anchor in cited:
            with self.subTest(anchor=anchor):
                self.assertIn(f'id="{anchor}"', reading)

    def test_the_jump_names_the_paragraph_to_go_back_to(self) -> None:
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        question = self.question_ids(html)[0]
        marked = self.submit(html, {question: "not in the paragraph"})
        block = next(b for b in self.blocks(marked) if f'id="question-{question}"' in b)
        self.assertIn("read that paragraph again", flat(block))
        self.assertIn("math-201-eigenvalues.pdf", block)


class TakingItAgainTests(QuizTestCase):
    def test_writing_it_again_replaces_the_questions(self) -> None:
        first = self.quiz(QuizModel(FROM_THE_MATERIAL))
        again = self.rewrite(
            QuizModel(
                fake_question(1, "What is the spectrum of A?", answer="the set of its eigenvalues")
            )
        )
        self.assertNotEqual(self.question_ids(first), self.question_ids(again))
        self.assertIn("What is the spectrum of A?", flat(again))

    def test_a_quiz_comes_back_the_same_when_it_is_not_rewritten(self) -> None:
        """The student has to be able to come back to the questions they read,
        because the answers they marked were marked against those questions."""
        html = self.quiz(QuizModel(FROM_THE_MATERIAL))
        self.assertEqual(self.question_ids(html), self.question_ids(self.page()))


class NothingToAskTests(QuizTestCase):
    def test_a_topic_pointing_at_no_slides_says_so_rather_than_writing_a_quiz(self) -> None:
        self.client.post("/courses/1/topics/add/", {"title": "Empty topic"})
        html = self.client.get("/courses/1/topics/").content.decode()
        empty = topic_pk(html, "Empty topic")
        response = self.client.get(f"/courses/1/topics/{empty}/quiz/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("No questions to sit", flat(response.content.decode()))