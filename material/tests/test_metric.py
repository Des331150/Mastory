"""Whether this is helping, read off the record with nobody asked.

Every test here goes in through HTTP and fakes only ``material.model.complete``,
so the metric and the two sides of the correlation are computed from the same
attempt, topic and exam rows a real run writes. That is the whole claim of the
ticket in one line: the number comes out of the record, so nothing was asked of
the student and nothing would have to be remembered afterwards.

The gate is the thing worth pinning down. Readiness is a mean over the topics
that have been sat and prints at half a course; this figure is a proportion of
the course, so it prints only when every topic has a sitting. A test that only
ever saw it agree with readiness would pass with the gate deleted, which is why
half a course is asserted to be enough for one figure and not the other.

The correlation needs a student on each side to be checkable at all, and v0 has
one identity: a test can be somebody else for a while, because the hardcoded user
is a setting rather than a login. That is what the last test case is for, and it
is the reason the page can say what it does about not having a comparison yet.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from fractions import Fraction

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from material.tests.helpers import (
    LINEAR_ALGEBRA_PDF,
    MockSittings,
    attribute,
    fake_topic,
    flat,
    on_a_monday,
    panel,
    pdf_upload,
    topic_pk,
    use_fake_model,
    use_temporary_media_root,
)

#: Four topics of one course, one slide each. Two more than the halfway mark and
#: four less than the whole course, so one fixture shows this gate refusing at
#: two sat, at three sat, and printing at four.
TOPICS = (
    ("Eigenvalues", [1]),
    ("Eigenspaces", [2]),
    ("Diagonalisation", [3]),
    ("Cayley-Hamilton", [4]),
)
NAMES = [title for title, _ in TOPICS]


class MetricTestCase(MockSittings, TestCase):
    """One course uploaded, its topic path confirmed, and nothing sat yet."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.upload_course(1, "Math 201")

    def upload_course(self, course: int, title: str) -> None:
        """Infer and confirm a topic path, the way the student does it."""
        self.client.post(
            "/courses/new/", {"title": title, "files": [pdf_upload(LINEAR_ALGEBRA_PDF)]}
        )
        claims = [fake_topic(topic, slides) for topic, slides in TOPICS]
        with use_fake_model(*claims):
            self.client.post(f"/courses/{course}/topics/infer/")
        self.client.post(f"/courses/{course}/topics/confirm/")

    def page(self, course: int = 1) -> str:
        response = self.client.get(f"/courses/{course}/cost/")
        self.assertEqual(response.status_code, 200)
        return flat(response.content.decode())

    def measure(self, page: str | None = None) -> str:
        """The share the page prints, or ``unreadable`` where it prints none."""
        shown = self.page() if page is None else page
        return attribute(panel(shown, "metric"), "data-metric")

    def count(self, page: str, name: str) -> int:
        """One of the counts on the metric panel, read as the page printed it."""
        return int(attribute(panel(page, "metric"), name))

    def arm(self, page: str, side: str, name: str) -> str:
        """One of the numbers on one side of the correlation."""
        block = page.split(f'data-side="{side}"')[1].split("</li>")[0]
        return attribute(block, name)

    def remove(self, *titles: str, course: int = 1) -> None:
        """Take topics off the path the way the student does, from the page."""
        html = self.client.get(f"/courses/{course}/topics/").content.decode()
        for title in titles:
            response = self.client.post(
                f"/courses/{course}/topics/{topic_pk(html, title)}/remove/", follow=True
            )
            self.assertEqual(response.status_code, 200)

    def cut(self, title: str, course: int = 1) -> None:
        """Take one topic off the week, through the plan page's own button."""
        html = self.client.get(f"/courses/{course}/topics/").content.decode()
        with on_a_monday():
            response = self.client.post(
                f"/courses/{course}/plan/topics/{topic_pk(html, title)}/cut/",
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('class="error"', flat(response.content.decode()))

    def sit_all(self, right: Fraction = Fraction(1)) -> None:
        """Sit every topic's quiz once, marking ``right`` of each one correct."""
        for title in NAMES:
            self.sit(title, right)


class ShareTests(MetricTestCase):
    """The share of the course the student has got through."""

    def test_the_metric_is_the_share_of_topics_over_the_line(self) -> None:
        """Four of four sat and got through: the whole share, printed. The line
        is four fifths rather than half because half is what counts as passing
        one sitting, and the question this figure asks is whether the student
        would get through the exam on the topic."""
        self.sit_all()

        page = self.page()
        self.assertEqual(self.measure(page), "100")
        self.assertEqual(self.count(page, "data-crossed"), 4)
        self.assertEqual(self.count(page, "data-total"), 4)
        self.assertIn("over the four in five mastery line", page)

    def test_a_share_of_a_whole_course_says_how_thin_the_evidence_is(self) -> None:
        """A hundred per cent off one sitting each is four guesses, and the
        readiness report says so beside the very same figures. A metric reading
        as certain on evidence the report calls a guess would be the one page in
        this product that flatters."""
        self.sit_all()

        self.assertIn("rests on one sitting, so it is one guess", self.page())

    def test_a_topic_short_of_the_line_is_not_counted_as_got_through(self) -> None:
        """Three quarters is a passing sitting and is not a topic the student
        would get through the exam on. The count is the number of topics over the
        line, so it is held to that rather than to the number of quizzes passed."""
        self.sit_all(Fraction(3, 4))

        page = self.page()
        self.assertEqual(self.measure(page), "0")
        self.assertEqual(self.count(page, "data-crossed"), 0)
        self.assertEqual(self.count(page, "data-weighed"), 4)

    def test_a_topic_nobody_has_sat_means_the_figure_cannot_be_read(self) -> None:
        """The gate, at three sat of four. A share worked out of the topics a
        student happened to quiz is a figure about those evenings wearing the
        course's percentage."""
        for title in NAMES[:3]:
            self.sit(title)

        page = self.page()
        self.assertEqual(self.measure(page), "unreadable")
        self.assertEqual(self.count(page, "data-crossed"), 3)
        self.assertEqual(self.count(page, "data-weighed"), 3)
        self.assertIn("1 topic of your course has no sitting at all", page)

    def test_half_a_course_is_enough_for_readiness_and_not_for_this_figure(self) -> None:
        """The two figures are gated in their own units, and this is where saying
        so matters. Readiness is the mean of what has been weighed, so half a
        course is a fair reading of it and the report prints a percentage. This
        is a proportion of the course itself, so the same two sittings of four
        must not print one: a gate counting the same thing as the figure it gates
        is how two of twenty being done gets reported as ten per cent."""
        for title in NAMES[:2]:
            self.sit(title)

        page = self.page()
        self.assertEqual(self.measure(page), "unreadable")
        self.assertEqual(self.count(page, "data-weighed"), 2)
        readiness = flat(self.client.get("/courses/1/readiness/").content.decode())
        self.assertIn('class="panel readiness measurable"', readiness)
        self.assertNotEqual(
            attribute(panel(readiness, "readiness"), "data-readiness"),
            "unmeasurable",
            "readiness should be printing a figure at half a course",
        )

    def test_the_refusal_says_which_topics_are_missing_the_way_the_report_does(self) -> None:
        """Two pages naming the topics that have nothing weighed on them must say
        it one way. A second sentence about the same fact is a second answer to
        it, and the report the student read a moment earlier is the one they
        will remember being told."""
        self.sit(NAMES[0])
        page = self.page()
        readiness = flat(self.client.get("/courses/1/readiness/").content.decode())
        note = readiness.split('class="unweighed-note">')[1].split("</p>")[0]

        self.assertIn("have no sitting at all", note)
        self.assertEqual(self.measure(page), "unreadable")
        self.assertIn(note, page)

    def test_a_course_of_one_topic_reads_as_a_share_of_one(self) -> None:
        """A course can come to a single topic after the student has cut the rest,
        and the sentence about it has to agree with itself: one topic is over the
        line, not one topic are."""
        self.remove(*NAMES[1:])
        self.sit(NAMES[0])

        page = self.page()
        self.assertEqual(self.measure(page), "100")
        self.assertEqual(self.count(page, "data-total"), 1)
        self.assertIn(
            "1 of this course's 1 topic is over the four in five mastery line", page
        )

    def test_a_topic_cut_off_the_week_is_still_one_the_figure_must_account_for(self) -> None:
        """A cut topic is still on the exam. Counting only the topics the student
        kept would let the figure be raised by deleting work rather than by
        knowing it, which is the one thing a measure of what somebody knows must
        not be."""
        with on_a_monday():
            self.client.post(
                "/courses/1/plan/settings/",
                {"exam_date": "", "days_per_week": "3", "hours_per_week": "12"},
                follow=True,
            )
        self.cut("Cayley-Hamilton")
        for title in NAMES[:3]:
            self.sit(title)

        page = self.page()
        self.assertEqual(self.count(page, "data-total"), 4)
        self.assertEqual(self.measure(page), "unreadable")
        self.assertIn("still on the exam, so it is still counted here", page)

    def test_a_course_with_no_topics_says_so_instead_of_a_share(self) -> None:
        """The proportion of no topics is not zero of the course: it is the
        absence of a course to have a proportion of, which is a different finding
        and gets its own sentence."""
        self.remove(*NAMES)

        page = self.page()
        self.assertEqual(self.count(page, "data-total"), 0)
        self.assertEqual(self.measure(page), "unreadable")
        self.assertIn("no topics on it", page)

    def test_nothing_on_this_page_asks_the_student_anything(self) -> None:
        """The metric is derived, not collected. A form on this page would mean
        the figure could only ever be read from the students who filled it in,
        which is the measurement the product turned down."""
        page = self.page()

        self.assertNotIn("<form", page)
        self.assertNotIn("<input", page)


class CorrelationTests(MetricTestCase):
    """The claim this product's mechanic makes, with both sides counted."""

    def test_a_student_who_never_failed_is_on_the_other_side(self) -> None:
        """Sat it once, got it right, and has never gone back to anything: they
        are in the cohort the claim is not about, and the page has to be able to
        put somebody there or the other side means nothing."""
        self.sit("Eigenvalues", Fraction(1))
        self.take_exam(Fraction(1))

        page = self.page()
        self.assertEqual(self.arm(page, "carried-on", "data-students"), "1")
        self.assertEqual(self.arm(page, "carried-on", "data-mean"), "100")
        self.assertEqual(self.arm(page, "went-back", "data-students"), "0")
        self.assertIn(
            "Nobody who has been back to a quiz they failed has sat the mock yet",
            page,
        )

    def test_a_student_who_failed_and_went_back_is_on_the_first_side(self) -> None:
        """The mechanic the product is built on: a failing sitting followed by
        another one on the same topic. It is read off the sittings rather than off
        a flag, because the product keeps no flag saying a student went back."""
        self.sit("Eigenvalues", Fraction(0))
        self.sit_again("Eigenvalues", Fraction(1))
        self.take_exam(Fraction(1, 2))

        page = self.page()
        self.assertEqual(self.arm(page, "went-back", "data-students"), "1")
        self.assertEqual(self.arm(page, "went-back", "data-mean"), "50")
        self.assertEqual(self.arm(page, "carried-on", "data-students"), "0")
        self.assertIn(
            "Nobody who has never been back to a failed quiz has sat the mock yet",
            page,
        )

    def test_no_mock_sat_on_either_side_means_no_scores_are_invented(self) -> None:
        """Nobody sitting the exam is not the same claim as everybody scoring
        nothing on it, and a mean of nothing must not be averaged into a zero."""
        self.sit("Eigenvalues", Fraction(1))

        page = self.page()
        for side in ("went-back", "carried-on"):
            self.assertEqual(self.arm(page, side, "data-mocks"), "0")
            self.assertEqual(self.arm(page, side, "data-mean"), "")
        self.assertIn("0 mocks sat", page)

    def test_the_whole_record_is_counted_and_not_only_this_course(self) -> None:
        """A student who has been on another course is still a student, and the
        claim is about students rather than about one course's page."""
        self.sit("Eigenvalues", Fraction(1))
        with self.as_another_student():
            self.upload_course(2, "Bio 110")
            self.sit("Eigenspaces", Fraction(0), course_id=2)
            self.sit_again("Eigenspaces", Fraction(1), course_id=2)

        page = self.page()
        self.assertEqual(self.arm(page, "went-back", "data-students"), "1")
        self.assertEqual(self.arm(page, "carried-on", "data-students"), "1")

    def test_a_student_on_each_side_puts_the_two_scores_beside_each_other(self) -> None:
        """What the claim being checkable means. One student went back after
        failing and scored 100%; the other never failed and scored 50%; the page
        prints both and stops there, because a difference worked out from one
        student on each side is arithmetic dressed up as a finding."""
        self.sit("Eigenvalues", Fraction(0))
        self.sit_again("Eigenvalues", Fraction(1))
        self.take_exam(Fraction(1))

        with self.as_another_student():
            self.upload_course(2, "Bio 110")
            self.sit("Eigenspaces", Fraction(1), course_id=2)
            self.take_exam(Fraction(0), course_id=2)

        page = self.page()
        self.assertEqual(attribute(panel(page, "correlation"), "data-comparable"), "yes")
        self.assertEqual(self.arm(page, "went-back", "data-mean"), "100")
        self.assertEqual(self.arm(page, "carried-on", "data-mean"), "0")
        self.assertIn("can be put", page)
        self.assertNotIn("Nobody who has", page)

    @contextmanager
    def as_another_student(self) -> Iterator[None]:
        """The product driven as a second student, on a course of their own.

        v0's identity is a setting rather than a login, so a test can be somebody
        else for a while. What they write are ordinary rows, which is the point:
        the claim is about students in the plural and cannot be checked against
        one of them.
        """
        user = get_user_model().objects.create(username="somebody-else")
        with override_settings(HARDCODED_USER_ID=user.pk):
            yield