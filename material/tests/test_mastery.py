"""Mastery and completion as two separate things.

The rule this file exists to pin down is one sentence: **finishing a topic and
knowing a topic are different questions, and the app stops conflating them.**
Every test here goes in through HTTP and fakes only ``material.model.complete``,
so the quiz, the marking, the completion and the weighting are all the real
application, and every number is read off the page the student is looking at -
because a mastery figure nobody can see is a claim rather than a feature.

The weighting is asserted as exact figures rather than as "went up", so a recency
discount quietly deleted or quietly doubled cannot pass as the rule it was
written for. The clock is pinned to a known Monday where the day a topic was
finished on is part of what is being asserted.

Every one of these assertions is the acceptance criterion: completing at a low
score still completes the topic, and mastery reflects the weighted attempt
history, read by a student through the same page.
"""

import re
from datetime import date, timedelta

from django.test import TestCase

from material.tests.helpers import (
    A_MONDAY,
    DEFAULT_QUESTION,
    LINEAR_ALGEBRA_PDF,
    QuizModel,
    QuizSittings,
    fake_topic,
    flat,
    on_a_monday,
    pdf_upload,
    topic_pk,
    use_fake_model,
    use_temporary_media_root,
)

TOPICS = (
    ("Eigenvalues", [2, 3]),
    ("Eigenspaces", [4]),
)
NAMES = [title for title, _ in TOPICS]

#: A Monday, so the day a topic is finished on can be read off the page. Stated
#: here rather than imported as a bare literal because the tests below name it as
#: the Monday this course was started on.

#: The Monday after it. An exam on a study day that is also the last day leaves
#: room for one session, which is how a test gets a topic with no session on the
#: week without inventing a course. Stated as a relation rather than a literal so
#: that moving one cannot silently stop being a Monday or stop being the next one.
THE_EXAM = A_MONDAY + timedelta(days=7)

#: A fortnight after the first sitting: a retake a student takes when they have
#: been getting on with something else, rather than one taken the same minute.
LATER = A_MONDAY + timedelta(weeks=2)

#: A question and an answer this fixture's paragraphs really contain, so the fake
#: model has something true to write about.
FROM_THE_MATERIAL = DEFAULT_QUESTION


class StandingTestCase(TestCase):
    """One course uploaded, its topic path confirmed, and a topic to quiz."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        claims = [fake_topic(title, slides) for title, slides in TOPICS]
        with use_fake_model(*claims):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/confirm/")

    # -- the week, when a test needs one --------------------------------------

    def build_week(
        self, *, days: str = "3", hours: str = "6", exam: str = "", sessions: int = 0
    ) -> None:
        """Build the week the way the student does, from the three numbers.

        ``sessions`` is how many sessions the week should end up with, asserted
        straight away: a rejected plan renders the page too, with an error on it
        and no week, so a test that only checked the status code would carry on
        through a plan that was never built.
        """
        with on_a_monday():
            response = self.client.post(
                "/courses/1/plan/settings/",
                {"exam_date": exam, "days_per_week": days, "hours_per_week": hours},
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        page = flat(response.content.decode())
        self.assertNotIn('class="error"', page, f"the week was refused: {page}")
        if sessions:
            self.assertEqual(len(self.planned_titles()), sessions)

    def plan(self) -> str:
        with on_a_monday():
            response = self.client.get("/courses/1/plan/")
        self.assertEqual(response.status_code, 200)
        return flat(response.content.decode())

    # -- reaching the quiz the way a student reaches it -----------------------

    def topic_id(self, title: str) -> str:
        return topic_pk(self.client.get("/courses/1/topics/").content.decode(), title)

    def quiz_url(self, title: str) -> str:
        return f"/courses/1/topics/{self.topic_id(title)}/quiz/"

    def page(self, title: str = "Eigenvalues") -> str:
        response = self.client.get(self.quiz_url(title))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # -- the quiz -------------------------------------------------------------

    def blocks(self, html: str) -> list[str]:
        return html.split('<li class="question')[1:]

    def question_ids(self, html: str) -> list[str]:
        return re.findall(r'id="question-(\d+)"', html)

    def options(self, html: str, question: str) -> list[str]:
        """The choices a question offers, in the order the page shows them."""
        block = next(b for b in self.blocks(html) if f'id="question-{question}"' in b)
        return re.findall(r'value="([^"]*)"', block)

    def with_n_right(self, html: str, n: int) -> dict[str, str]:
        """Answers marking the first ``n`` questions right and the rest wrong.

        The fake model writes the option the paragraph contains first, so the
        first choice is the right one and the last is not; a test asking for a
        particular score should not have to know what the model wrote. Two choices
        at least is asserted, because a question offering one cannot be got wrong
        and would quietly turn a test asking for a zero into a perfect sitting.
        """
        questions = self.question_ids(html)
        self.assertTrue(questions, "there are no questions on this page to answer")
        answers = {}
        for index, question in enumerate(questions):
            choices = self.options(html, question)
            self.assertGreaterEqual(
                len(choices), 2, f"question {question} offers nothing to get wrong"
            )
            answers[question] = choices[0 if index < n else -1]
        return answers

    def mark(self, html: str, answers: dict[str, str]) -> str:
        """Mark a sitting the way the form does: one field per question.

        The url and the quiz the form was drawn from are read off the page rather
        than built, so the sitting is marked on the quiz the student was actually
        looking at, as a browser would send it.
        """
        action = re.search(r'action="([^"]+)"', flat(html))
        assert action is not None, "the quiz page offers no form to mark"
        which = re.search(r'name="quiz" value="(\d+)"', html)
        assert which is not None, "the form does not say which quiz it is for"
        response = self.client.post(
            action.group(1),
            {
                "nonsense": "",
                "quiz": which.group(1),
                **{f"q-{key}": value for key, value in answers.items()},
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def rewrite(self, title: str) -> str:
        """Ask for different questions, which is what a retake means."""
        response = self.client.post(f"{self.quiz_url(title)}rewrite/", follow=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def sit(self, n_right: int = 4, *, title: str = "Eigenvalues") -> str:
        """Sit this topic's quiz once, marking ``n_right`` of the questions right."""
        model = QuizModel(FROM_THE_MATERIAL)
        model.use(self)
        with on_a_monday():
            html = self.page(title)
            return self.mark(html, self.with_n_right(html, n_right))

    def sit_again(
        self,
        n_right: int = 4,
        *,
        title: str = "Eigenvalues",
        moment: date = A_MONDAY,
    ) -> str:
        """Take the quiz again: write a new set of questions, then sit that one.

        A retake has to be a new quiz, or the same four questions are being sat
        twice, which is the thing regeneration exists to stop.

        ``moment`` is the day the retake is sat on, which is what a retake means
        in the record: pinned to the same Monday as the first sitting unless a
        test says otherwise, because a retake on the day the topic was finished is
        not the case the record has to answer.
        """
        model = QuizModel(FROM_THE_MATERIAL)
        model.use(self)
        with on_a_monday(moment):
            rewritten = self.rewrite(title)
            self.assertTrue(
                self.question_ids(rewritten), "the rewrite produced no questions to sit"
            )
            return self.mark(rewritten, self.with_n_right(rewritten, n_right))

    # -- reading the two numbers off the page ---------------------------------

    def mastery(self, html: str) -> str:
        """The mastery the page shows, or "unmeasured" where it shows none.

        Read out of this topic's own standing block rather than the whole page,
        because on a page carrying several topics a whole-page search answers with
        whichever figure came first and a test would be asserting about the wrong
        topic without ever saying so.
        """
        found = re.search(r'data-mastery="([^"]+)"', flat(self.standing_block(html)))
        assert found is not None, "the page says nothing about this topic's mastery"
        return found.group(1)

    def completed(self, html: str) -> bool:
        """Whether the page says this topic is finished."""
        found = re.search(r'data-complete="(yes|no)"', flat(self.standing_block(html)))
        assert found is not None, "the page says nothing about whether this is finished"
        return found.group(1) == "yes"

    def standing_block(self, html: str) -> str:
        """The panel on a quiz page that reports one topic's standing."""
        found = re.search(
            r'<section class="panel topic-standing">(.*?)</section>', flat(html), re.S
        )
        assert found is not None, "the quiz page reports no standing for this topic"
        return found.group(1)

    def standing_row(self, title: str, plan: str | None = None) -> str:
        """The plan page's row for one topic, found the way a student finds it.

        By the row that names the topic, not by where in the row its name sits, so
        that reordering two lines of markup is not a failure of this test.
        """
        page = flat(plan if plan is not None else self.plan())
        for block in page.split('<li class="standing-row"')[1:]:
            row = block.split("</li>")[0]
            if re.search(rf'>{re.escape(title)}</p>', row):
                return row
        raise AssertionError(f"no standing row for {title!r} on the plan")

    def session_block(self, title: str) -> str:
        """One session as the plan page renders it, cut out of its week.

        Found through the session's own heading rather than through the title
        anywhere on the page, because the next-session card and the standing
        table both name a topic the student is looking at without listing its
        session.
        """
        for block in self.plan().split('<li class="session')[1:]:
            if f'<h3 class="session-topic">{title}</h3>' in block:
                return block.split("</li>")[0]
        raise AssertionError(f"the plan has no session for {title!r}")

    def session_id(self, title: str) -> str:
        found = re.search(r"/sessions/(\d+)/", self.session_block(title))
        assert found is not None, f"no session url for {title!r}"
        return found.group(1)

    def mark_session(self, title: str, state: str) -> None:
        with on_a_monday():
            response = self.client.post(
                f"/courses/1/plan/sessions/{self.session_id(title)}/", {"state": state}
            )
        self.assertEqual(response.status_code, 302)

    # -- the plan's own lists -------------------------------------------------

    def planned_titles(self) -> list[str]:
        """The topics the week has given a session."""
        page = self.plan()
        return [
            title
            for title in NAMES
            if f'<h3 class="session-topic">{title}</h3>' in page
        ]

    def unplaced_titles(self) -> list[str]:
        """The topics the plan says have no session on it, as it names them.

        Cut out by the panel that holds them rather than by the two headings
        either side of it, so a renamed heading cannot turn this into a silent
        empty list - which is what would let the unplaced topics disappear from
        the plan without a single test noticing.
        """
        for section in self.plan().split("<section")[1:]:
            panel = section.split("</section>")[0]
            if "Not before your exam" in panel:
                return re.findall(r"<li>([^<]+)</li>", panel)
        return []


class CompletionTests(StandingTestCase):
    """Finishing is binary, and it happens at any score."""

    def test_sitting_a_topic_quiz_finishes_the_topic(self) -> None:
        """The plainest statement of the rule: a sitting puts the topic in the
        student's record, with the day it happened."""
        html = self.sit(n_right=4)
        self.assertTrue(self.completed(html))
        self.assertIn("Finished, on 2 November 2026", flat(html))

    def test_finishing_a_topic_is_not_waiting_on_a_passing_score(self) -> None:
        """The whole of the rule. A sitting that got nothing right has still
        worked the topic through, because completion is what takes a topic off
        the outstanding work: gating it on a score would make the one hard topic
        of a course the one a student can never get past.
        """
        marked = self.sit(n_right=0)
        self.assertIn("You got 0 of 4 right", flat(marked))
        self.assertIn("has still finished this topic", flat(marked))
        self.assertTrue(self.completed(marked))

    def test_finishing_a_topic_takes_it_out_of_the_work_still_waiting(self) -> None:
        """Completion gates the schedule. A topic in the student's record is not
        outstanding work whatever it scored, so the week reads it as done and the
        student is refused a cut on it."""
        self.build_week(sessions=2)
        self.sit(n_right=0)

        self.assertIn('data-state="done"', self.session_block("Eigenvalues"))

        response = self.client.post(
            f"/courses/1/plan/topics/{self.topic_id('Eigenvalues')}/cut/", follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("already in your record", flat(response.content.decode()))

    def test_finishing_a_topic_twice_keeps_the_day_it_was_finished(self) -> None:
        """A retake is a student going back over a topic they finished a fortnight
        ago. The day they finished it is the day the record should say they did
        it, so the retake's date must not overwrite it."""
        self.sit(n_right=4)
        again = self.sit_again(n_right=4, moment=LATER)

        self.assertIn("Finished, on 2 November 2026", flat(again))
        self.assertNotIn("16 November 2026", flat(again))

    def test_sitting_the_quiz_finishes_a_topic_the_student_had_skipped(self) -> None:
        """A skip and a finish are one column, not two, so working through a topic
        after deciding to skip it finishes it. Marking a session done has always
        overwritten a skip; sitting the quiz must not leave the same topic recorded
        as two different things at once.
        """
        self.build_week(sessions=2)
        self.mark_session("Eigenvalues", "skipped")

        self.sit(n_right=2)

        self.assertIn('data-state="done"', self.session_block("Eigenvalues"))
        self.assertIn('data-complete="yes"', self.standing_row("Eigenvalues"))

    def test_a_topic_finished_by_its_quiz_is_not_one_that_missed_out(self) -> None:
        """A topic can be finished before a week has ever scheduled it, so it has
        no session at all. Listing it beside the topics that missed out would
        call a finished topic a shortfall."""
        self.build_week(days="1", hours="1", exam=THE_EXAM.isoformat(), sessions=1)
        missed_out = self.unplaced_titles()
        self.assertEqual(
            len(missed_out), 1, "the week should leave one topic without a session"
        )

        self.sit(n_right=0, title=missed_out[0])

        self.assertEqual(
            self.unplaced_titles(), [], "a finished topic has not missed out"
        )
        self.assertIn('data-complete="yes"', self.standing_row(missed_out[0]))

    def test_topics_not_finished_stay_visible_in_the_plan(self) -> None:
        """Finishing one topic must not thin out the list of the rest: they are
        the plan, and a plan that quietly drops the rest of the course is not a
        plan a student can keep. The list of topics with no session is read and
        its length pinned before anything is asserted about it, because a helper
        that quietly returns nothing when the panel is missing would make this
        test pass on a plan that had lost the topics entirely.
        """
        self.build_week(days="1", hours="1", exam=THE_EXAM.isoformat(), sessions=1)
        missed_out = self.unplaced_titles()
        self.assertEqual(len(missed_out), 1, "the week should leave one topic off it")

        self.sit(n_right=0, title=self.planned_titles()[0])

        self.assertEqual(
            self.unplaced_titles(),
            missed_out,
            "the topic with no session must still be named on the plan",
        )
        for title in missed_out:
            row = self.standing_row(title)
            self.assertIn('data-complete="no"', row)
            self.assertIn('data-mastery="unmeasured"', row)

    def test_rebuilding_the_week_does_not_invent_a_session_for_a_finished_topic(
        self,
    ) -> None:
        """A topic finished by its quiz was never scheduled, and a rebuild has no
        business putting one on the calendar for it: the session would land on a
        day the student never planned to study, would spend their weekly budget
        on work they are not going to do, and would add one to the denominator of
        the share they are shown.
        """
        self.build_week(days="1", hours="2", exam=THE_EXAM.isoformat(), sessions=1)
        finished = self.unplaced_titles()[0]
        self.sit(n_right=0, title=finished)
        self.assertIn("You have done 0 of 1 session", self.plan())

        self.build_week(days="1", hours="2", exam=THE_EXAM.isoformat(), sessions=1)

        self.assertEqual(len(self.planned_titles()), 1)
        self.assertNotIn(finished, self.planned_titles())
        self.assertIn("You have done 0 of 1 session", self.plan())


class MasteryTests(StandingTestCase):
    """Mastery is a separate number, from the sittings, counting the later ones more."""

    def test_a_topic_nobody_has_sat_is_unmeasured_rather_than_zero(self) -> None:
        """Zero would be a claim - that the student knows nothing - which is not
        what the absence of a sitting says."""
        html = self.page()
        self.assertEqual(self.mastery(html), "unmeasured")
        self.assertFalse(self.completed(html))
        self.assertIn("nothing has been weighed on it yet", flat(html))

    def test_mastery_is_the_score_of_the_only_sitting(self) -> None:
        """One sitting, so nothing to discount and nothing to choose between:
        mastery is exactly what they scored. That is what makes "mastery comes
        from the best scored attempt" true rather than approximate.
        """
        marked = self.sit(n_right=2)
        self.assertIn("You got 2 of 4 right", flat(marked))
        self.assertEqual(self.mastery(marked), "50")

    def test_mastery_is_the_best_scored_attempt_on_the_topic(self) -> None:
        """A student who knew this and has since muddled through still knows some
        of it, so mastery comes from the best sitting rather than from the latest
        one or from an average of the two. Staged with the *earlier* sitting
        scoring higher, which is the only shape in which "best" and "latest" are
        different questions.
        """
        self.sit(n_right=4)
        self.sit_again(n_right=1)

        self.assertEqual(self.mastery(self.page()), "70")

    def test_a_later_attempt_counts_for_more_than_an_earlier_one(self) -> None:
        """Four out of four, then three. The later sitting scored less, and it
        still wins: 75% counted whole against 100% discounted once. Reading the
        best sitting at face value would say 100%, and reading the latest would
        happen to agree with the right answer here - which is why the scores have
        to differ for the rule to be pinned at all.
        """
        self.sit(n_right=4)
        self.sit_again(n_right=3)

        self.assertEqual(self.mastery(self.page()), "75")

    def test_a_lucky_early_guess_is_discounted_rather_than_counted_in_full(self) -> None:
        """A perfect first sitting and a weaker one after it. The later sitting
        counts for more, so the figure is the best one discounted: not the best
        one at face value, which would let one good afternoon outrank everything
        the student has done since, and not the later score, which would say a
        student who once knew this had forgotten all of it.
        """
        self.sit(n_right=4)
        self.sit_again(n_right=2)

        self.assertEqual(self.mastery(self.page()), "70")

    def test_the_discount_compounds_with_every_sitting_since(self) -> None:
        """One perfect sitting, then half marks again and again. The best sitting
        is discounted once for each sitting that has come after it - 100, then 70,
        then 49 - until a later sitting counts its own score in full and takes the
        figure. The exponent is pinned at every step, because "discounted once per
        sitting since" is only true if the counting is.
        """
        self.sit(n_right=4)
        self.assertEqual(self.mastery(self.page()), "100")

        self.sit_again(n_right=2)
        self.assertEqual(self.mastery(self.page()), "70")

        self.sit_again(n_right=1)
        self.assertEqual(self.mastery(self.page()), "49")

    def test_a_tie_is_settled_by_which_sitting_came_last(self) -> None:
        """The same score twice is the one case where "best" and "latest" could
        disagree. The later sitting wins, because it is not discounted and the
        earlier one is."""
        self.sit(n_right=4)
        self.sit_again(n_right=4)

        self.assertEqual(self.mastery(self.page()), "100")

    def test_the_most_recent_sitting_always_counts_its_whole_score(self) -> None:
        """The discount applies to sittings behind, never to the latest one, so a
        long history cannot quietly walk a topic the student is getting right down
        towards nothing - and the figure stays distinguishable from the zero this
        page prints for a topic nobody has sat.
        """
        self.sit(n_right=2)
        for _ in range(15):
            self.sit_again(n_right=2)

        self.assertEqual(self.mastery(self.page()), "50")
        self.assertNotEqual(self.mastery(self.page()), "unmeasured")

    def test_mastery_stays_a_share_across_a_mixed_history(self) -> None:
        """Criterion 3 as a property rather than a claim. Four sittings whose
        scores fall away from all of it to a quarter of it, and however the
        weights land the page prints a share of one topic: never more than all of
        it, never less than none of it, and never the same number twice in a row
        by accident.
        """
        seen = []
        self.sit(n_right=4)
        seen.append(self.mastery(self.page()))
        for n_right in (3, 2, 1):
            self.sit_again(n_right=n_right)
            seen.append(self.mastery(self.page()))

        for value in seen:
            with self.subTest(mastery=value):
                self.assertGreaterEqual(int(value), 0)
                self.assertLessEqual(int(value), 100)
        self.assertEqual(len(set(seen)), len(seen), f"the history stopped moving: {seen}")

    def test_completion_and_mastery_are_two_numbers_on_the_same_page(self) -> None:
        """The acceptance criterion in one test: a topic finished at half marks is
        finished, and how much of it the student knows is a different figure on
        the same page. A page showing only one of them either flatters the
        student who finished everything or hides the topic they finished without
        knowing it.
        """
        self.sit(n_right=2)
        html = self.page()

        self.assertTrue(self.completed(html))
        self.assertEqual(self.mastery(html), "50")
        self.assertIn("Where you are on this topic", flat(html))
        self.assertIn("Finishing says you have worked through it", flat(html))

    def test_the_plan_shows_completion_and_mastery_as_separate_columns(self) -> None:
        """Both numbers, for a topic either side of finishing, so that "which
        topics have I done" and "which of them do I know" are answered together
        rather than by two pages that can disagree.

        The finished topic has sat twice with different scores, because the plan
        reads its sittings on its own path through the code rather than the quiz
        page's, and a single sitting would leave that path indistinguishable from
        any weighting at all. The two pages are then held to one number: the same
        topic, the same figure, whichever page the student opens.
        """
        self.build_week(sessions=2)
        self.sit(n_right=4)
        self.sit_again(n_right=3)

        finished = self.standing_row("Eigenvalues")
        self.assertIn('data-complete="yes"', finished)
        self.assertIn('data-mastery="75"', finished)
        self.assertEqual(
            self.mastery(self.page("Eigenvalues")),
            "75",
            "the plan and the quiz page must not print two mastery figures",
        )

        ahead = self.standing_row("Eigenspaces")
        self.assertIn('data-complete="no"', ahead)
        self.assertIn('data-mastery="unmeasured"', ahead)

    def test_a_skipped_topic_is_not_reported_as_finished(self) -> None:
        """Skipping takes a topic off the outstanding work without the student
        having worked through it, so the standing column must say skipped and not
        finished - even though the plan has already stopped waiting on it. Reading
        the whole of the record as "finished" would print one topic two ways on one
        page, and would tell a student who decided not to do this that they had
        done it.
        """
        self.build_week(sessions=2)
        self.mark_session("Eigenvalues", "skipped")

        row = self.standing_row("Eigenvalues")
        self.assertIn('data-state="skipped"', row)
        self.assertIn('data-complete="no"', row)
        self.assertNotIn("finished", row)

        html = self.page("Eigenvalues")
        self.assertFalse(self.completed(html))
        self.assertIn("Skipped on", flat(html))
        self.assertIn("Skipping is not failing at it", flat(html))
        self.assertIn("Skipped, so nothing has been weighed on it", flat(html))

    def test_a_topic_finished_without_a_sitting_says_so_rather_than_scoring_zero(
        self,
    ) -> None:
        """Completion can also arrive from a session the student marked, and then
        there is nothing to weigh. Printing 0% there would claim they know nothing
        about a topic they finished, and it is the one case where the two columns
        have to disagree: finished, and unweighed.
        """
        self.build_week(sessions=2)
        self.mark_session("Eigenvalues", "done")

        html = self.page("Eigenvalues")
        self.assertTrue(self.completed(html))
        self.assertEqual(self.mastery(html), "unmeasured")
        self.assertIn("Finished without sitting the quiz", flat(html))

        row = self.standing_row("Eigenvalues")
        self.assertIn('data-complete="yes"', row)
        self.assertIn('data-mastery="unmeasured"', row)
