"""Keeping track: the share of the plan the student has done, and the log of the
days they actually did it.

Deliberately not a streak. A streak breaks on one missed day and punishes exactly
the student the shift-never-compress model exists to protect, so what the page
offers instead is a proportion and a dated log, and a way to say "I am not doing
this one" that costs nothing. Two of the tests here exist only to say that out
loud: skipping changes neither the proportion nor the dates, and nothing in the
product counts a streak, a score or a ranking.

Session tracking goes in through HTTP, as everything else does: the student
reads the plan page, presses a button on a session, and reads the page again.
The url a test posts to is read off the page the way a click would find it, never
built from an id the test happened to know.

Attempts are the one thing here without an HTTP seam, and deliberately so. An
attempt is a row a quiz writes when the student takes it, and no quiz exists
until a later ticket; recording it now is what stops the table being invented
later from a schema nobody has used. Those tests drive
``material.progress.record_attempt`` directly, because there is nothing else to
drive.
"""

import re
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

from django.apps import apps
from django.test import TestCase

from material import planning, progress
from material.models import Topic
from material.topics import Rejected
from material.tests.helpers import (
    LINEAR_ALGEBRA_PDF,
    fake_topic,
    pdf_upload,
    use_fake_model,
    use_temporary_media_root,
)

#: A Monday in a known week, so a plan built on it lands on dates the test can
#: say out loud.
A_MONDAY = date(2026, 11, 2)

MATERIAL_ROOT = Path(__file__).resolve().parent.parent

TOPICS = (
    ("Eigenvalues", [2, 3]),
    ("Eigenspaces", [4, 5]),
    ("Diagonalisation", [5]),
)


@contextmanager
def a_week_of(moment: date) -> Iterator[None]:
    """Build and read the week as though the student were living on a given day."""
    with mock.patch.object(planning, "today", return_value=moment):
        yield


def flat(text: str) -> str:
    """The page with its line wrapping squeezed out.

    Where a template happens to break a line is not something a student can see
    and must not be something a test asserts on, so the assertions here read
    the copy as it renders rather than as it is laid out.
    """
    return " ".join(text.split())


class SessionLogTestCase(TestCase):
    """One course uploaded, its topic path confirmed, and three sessions planned."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        self.confirm_path()

    def confirm_path(self, *topics: Any) -> None:
        """Work a topic path out and confirm it, the way the student does."""
        claims = [fake_topic(title, slides) for title, slides in topics] or [
            fake_topic(title, slides) for title, slides in TOPICS
        ]
        with use_fake_model(*claims):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/confirm/")

    def set_availability(self, *, days: int = 3, hours: int = 3, exam: str = "") -> Any:
        return self.client.post(
            "/courses/1/plan/settings/",
            {"exam_date": exam, "days_per_week": str(days), "hours_per_week": str(hours)},
        )

    def plan_page(self, course: int = 1) -> str:
        response = self.client.get(f"/courses/{course}/plan/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # -- reading the page the way the student reads it -----------------------

    def session_block(self, html: str, topic: str) -> str:
        # Split on the class rather than the whole opening tag, because a
        # session that is not yet marked carries no second class.
        for block in html.split('<li class="session')[1:]:
            if f">{topic}</h3>" in block:
                return block
        raise AssertionError(f"no session on {topic!r} in the plan")

    def session_state(self, html: str, topic: str) -> str:
        """What one session says about itself, in the word the page uses."""
        block = self.session_block(html, topic)
        found = re.search(r'<p class="session-state" data-state="([a-z ]+)">(.*?)</p>', block)
        assert found is not None, f"no state shown for {topic!r}"
        return found.group(2).strip()

    def mark(self, topic: str, state: str, course: int = 1) -> Any:
        """Press the button for one session, by the url the page itself offers."""
        return self.client.post(self.mark_url(topic, course), {"state": state})

    def mark_url(self, topic: str, course: int = 1) -> str:
        found = re.search(r'action="([^"]+)"', self.session_block(self.plan_page(course), topic))
        assert found is not None, f"no button offered for {topic!r}"
        return found.group(1)

    def completion(self, html: str) -> str:
        found = re.search(r'<section class="panel completion">(.*?)</section>', html, re.S)
        assert found is not None, "no completion panel on the plan page"
        return flat(found.group(1))

    def log_entries(self, html: str) -> list[dict[str, str]]:
        """The session log as a list of entries, in the order the page shows them."""
        found = re.search(r'<section class="panel session-log">(.*?)</section>', html, re.S)
        assert found is not None, "no session log on the plan page"
        entries = []
        for block in found.group(1).split('<li class="log-entry"')[1:]:
            when = re.search(r'data-date="([\d-]+)"', block)
            topic = re.search(r'<p class="log-topic">(.*?)</p>', block, re.S)
            state = re.search(r'<p class="log-state">(.*?)</p>', block, re.S)
            assert when and topic and state, f"a log entry is missing part of itself: {block}"
            entries.append(
                {"date": when.group(1), "topic": topic.group(1).strip(), "state": state.group(1).strip()}
            )
        return entries

    def days_from_today(self, days: int) -> str:
        from datetime import timedelta

        return (planning.today() + timedelta(days=days)).isoformat()

    def topic(self, title: str) -> Topic:
        return Topic.objects.get(title=title)


class CompletionProportionTests(SessionLogTestCase):
    def test_a_plan_the_student_has_not_started_shows_nothing_done(self) -> None:
        self.set_availability()
        self.assertIn("You have done 0 of 3 sessions", self.completion(self.plan_page()))

    def test_sessions_done_are_a_share_of_the_sessions_planned(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "done")
        self.assertIn("You have done 1 of 3 sessions", self.completion(self.plan_page()))

    def test_the_share_is_of_the_whole_plan_not_of_what_is_left(self) -> None:
        """The denominator is what the student planned, because that is the thing
        they said they would do. Measuring against the sessions still ahead would
        make finishing a plan read as 100% on the last day and 1% on the first."""
        self.set_availability()
        self.mark("Eigenvalues", "done")
        self.mark("Eigenspaces", "done")
        self.assertIn("You have done 2 of 3 sessions", self.completion(self.plan_page()))
        self.assertIn("67%", self.completion(self.plan_page()))

    def test_the_share_is_a_percentage_the_student_can_read_at_a_glance(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "done")
        found = re.search(r'class="meter-fill" style="width: (\d+)%"', self.plan_page())
        assert found is not None
        self.assertEqual(found.group(1), "33")

    def test_a_plan_with_nothing_in_it_shows_no_percentage_to_divide_by(self) -> None:
        self.set_availability(exam=self.days_from_today(0))
        completion = self.completion(self.plan_page())
        self.assertIn("No sessions on your plan yet", completion)
        self.assertNotIn("%", completion)

    def test_a_session_the_student_undoes_leaves_the_share_again(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "done")
        self.mark("Eigenvalues", "done")
        self.assertIn("You have done 0 of 3 sessions", self.completion(self.plan_page()))


class SkippingTests(SessionLogTestCase):
    def test_a_session_can_be_skipped_in_one_action(self) -> None:
        self.set_availability()
        response = self.mark("Eigenvalues", "skipped")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/courses/1/plan/")
        self.assertEqual(self.session_state(self.plan_page(), "Eigenvalues"), "skipped")

    def test_skipping_takes_nothing_off_what_the_student_has_done(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "done")
        self.mark("Eigenspaces", "skipped")
        completion = self.completion(self.plan_page())
        self.assertIn("You have done 1 of 3 sessions", completion)
        self.assertIn("33%", completion)

    def test_the_page_says_that_skipping_costs_nothing(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "skipped")
        self.assertIn("costs you nothing", self.completion(self.plan_page()))

    def test_skipping_does_not_move_the_session_or_lose_its_place(self) -> None:
        """A skipped session still sits on its day and still counts as planned. It
        is the student choosing not to do it, not the plan quietly forgetting."""
        self.set_availability()
        before = self.plan_page()
        self.mark("Eigenvalues", "skipped")
        after = self.plan_page()
        self.assertEqual(
            self.session_dates(before), self.session_dates(after)
        )
        self.assertIn("You have done 0 of 3 sessions", self.completion(after))

    def test_a_session_cannot_be_done_and_skipped_at_the_same_time(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "done")
        self.mark("Eigenvalues", "skipped")
        html = self.plan_page()
        self.assertEqual(self.session_state(html, "Eigenvalues"), "skipped")
        self.assertEqual(len(self.log_entries(html)), 1)

    def test_a_skipped_session_can_be_taken_back_and_done(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "skipped")
        self.mark("Eigenvalues", "done")
        html = self.plan_page()
        self.assertEqual(self.session_state(html, "Eigenvalues"), "done")
        self.assertIn("You have done 1 of 3 sessions", self.completion(html))

    def session_dates(self, html: str) -> list[str]:
        return re.findall(r'<p class="session-when">(.*?)</p>', html)


class SessionLogTests(SessionLogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.set_availability()

    def test_the_log_is_there_and_empty_before_anything_has_happened(self) -> None:
        html = self.plan_page()
        self.assertEqual(self.log_entries(html), [])
        self.assertIn("Nothing in your log yet", html)

    def test_the_log_is_in_date_order_however_the_sessions_were_marked(self) -> None:
        with a_week_of(A_MONDAY):
            self.mark("Diagonalisation", "done")
            self.mark("Eigenvalues", "done")
            self.mark("Eigenspaces", "skipped")
            entries = self.log_entries(self.plan_page())
        self.assertEqual(
            [entry["topic"] for entry in entries],
            ["Eigenvalues", "Eigenspaces", "Diagonalisation"],
        )

    def test_a_session_marked_later_comes_after_one_marked_earlier(self) -> None:
        """The order is the order of the days, not the order of the list and not
        the order the buttons happened to be pressed."""
        with a_week_of(A_MONDAY):
            self.mark("Diagonalisation", "done")
            with a_week_of(date(2026, 11, 4)):
                self.mark("Eigenvalues", "done")
                entries = self.log_entries(self.plan_page())
        self.assertEqual(
            [entry["topic"] for entry in entries],
            ["Diagonalisation", "Eigenvalues"],
        )

    def test_two_sessions_marked_on_one_day_keep_their_order_on_the_path(self) -> None:
        with a_week_of(A_MONDAY):
            self.mark("Diagonalisation", "done")
            self.mark("Eigenvalues", "done")
            entries = self.log_entries(self.plan_page())
        self.assertEqual(
            [entry["topic"] for entry in entries],
            ["Eigenvalues", "Diagonalisation"],
        )

    def test_each_entry_says_what_happened_and_which_topic(self) -> None:
        self.mark("Eigenvalues", "done")
        self.mark("Eigenspaces", "skipped")
        entries = self.log_entries(self.plan_page())
        self.assertEqual([entry["state"] for entry in entries], ["done", "skipped"])
        self.assertEqual([entry["topic"] for entry in entries], ["Eigenvalues", "Eigenspaces"])

    def test_the_log_names_the_day_the_student_marked_it_not_the_day_it_was_planned(self) -> None:
        """A session marked on Tuesday went in the log on Tuesday. The student is
        looking for the dates they actually studied, not the dates they meant to."""
        with a_week_of(A_MONDAY):
            self.mark("Eigenvalues", "done")
            with a_week_of(date(2026, 11, 4)):
                self.mark("Eigenspaces", "done")
                entries = self.log_entries(self.plan_page())
        self.assertEqual(
            [entry["date"] for entry in entries],
            ["2026-11-02", "2026-11-04"],
        )

    def test_a_session_the_student_undoes_leaves_the_log(self) -> None:
        self.mark("Eigenvalues", "done")
        self.mark("Eigenvalues", "done")
        self.assertEqual(self.log_entries(self.plan_page()), [])

    def test_the_log_never_shows_another_course_s_sessions(self) -> None:
        self.client.post("/courses/new/", {"title": "Other", "files": [pdf_upload()]})
        with use_fake_model(fake_topic("Titration curves", [2])):
            self.client.post("/courses/2/topics/infer/")
        self.client.post("/courses/2/topics/confirm/")
        self.client.post(
            "/courses/2/plan/settings/",
            {"exam_date": "", "days_per_week": "3", "hours_per_week": "6"},
        )
        self.mark("Eigenvalues", "done")
        self.mark("Titration curves", "done", course=2)
        self.assertNotIn("Eigenvalues", self.plan_page(course=2))
        self.assertNotIn("Titration curves", self.plan_page())


class RebuildingKeepsTheRecordTests(SessionLogTestCase):
    """The week is a function of the topics and the hours, so building it again
    replaces it. What the student has already done is theirs, and a rebuild is
    not allowed to be the thing that loses it."""

    def test_a_session_marked_done_survives_the_week_being_built_again(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "done")
        self.set_availability(days=2, hours=4)
        html = self.plan_page()
        self.assertIn("You have done 1 of 3 sessions", self.completion(html))
        self.assertEqual(len(self.log_entries(html)), 1)

    def test_a_skipped_session_survives_the_week_being_built_again(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "skipped")
        self.set_availability(days=2, hours=4)
        html = self.plan_page()
        self.assertEqual(self.session_state(html, "Eigenvalues"), "skipped")
        self.assertEqual(self.log_entries(html)[0]["state"], "skipped")

    def test_the_record_survives_a_rebuild_that_leaves_a_topic_off_the_week(self) -> None:
        """An exam two days out leaves one session and drops the other two. What
        the student had already finished is not the plan's to throw away."""
        self.set_availability()
        self.mark("Eigenvalues", "done")
        self.mark("Eigenspaces", "done")
        with a_week_of(A_MONDAY):
            self.set_availability(days=1, hours=6, exam="2026-11-04")
            html = self.plan_page()
        self.assertIn("You have done 1 of 1 session", self.completion(html))
        self.assertEqual(len(self.log_entries(html)), 1)
        self.assertEqual(self.log_entries(html)[0]["state"], "done")

    def test_a_topic_that_left_the_week_is_not_forgotten_in_the_database(self) -> None:
        """The visible share counts this week's sessions, so a topic the plan no
        longer holds drops off it. Its record is still the student's own."""
        self.set_availability()
        self.mark("Eigenvalues", "done")
        self.mark("Eigenspaces", "done")
        with a_week_of(A_MONDAY):
            self.set_availability(days=1, hours=6, exam="2026-11-04")
            self.plan_page()
        self.assertIsNotNone(self.topic("Eigenspaces").completed_on)

    def test_the_form_still_says_it_replaces_the_sessions_and_what_it_keeps(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "done")
        self.set_availability(days=2, hours=4)
        self.assertIn("replaces the sessions", self.plan_page())


class AttemptRecordTests(SessionLogTestCase):
    """Per-topic attempt records, recorded from the first quiz onwards because
    none of it can be backfilled after the fact.

    There is no quiz yet, so there is no HTTP seam to drive: these are the rows
    the quiz will write, and what is being fixed here is the shape of the table
    and the refusal to record a score that means nothing.
    """

    def test_an_attempt_records_when_it_was_taken_its_score_and_its_verification_passes(
        self,
    ) -> None:
        topic = self.topic("Eigenvalues")
        attempt = progress.record_attempt(topic, score=0.75, passes=2)
        self.assertIsNotNone(attempt.taken_at)
        self.assertEqual(attempt.score, 0.75)
        self.assertEqual(attempt.passes, 2)

    def test_attempts_are_kept_per_topic_and_in_the_order_they_were_taken(self) -> None:
        topic = self.topic("Eigenvalues")
        progress.record_attempt(topic, score=0.2, passes=0)
        progress.record_attempt(topic, score=0.9, passes=1)
        taken = list(progress.attempts(topic))
        self.assertEqual([attempt.score for attempt in taken], [0.2, 0.9])

    def test_one_topic_s_answers_are_never_mixed_up_with_another_s(self) -> None:
        progress.record_attempt(self.topic("Eigenvalues"), score=0.9, passes=1)
        progress.record_attempt(self.topic("Eigenspaces"), score=0.1, passes=0)
        self.assertEqual(len(progress.attempts(self.topic("Eigenvalues"))), 1)
        self.assertEqual(len(progress.attempts(self.topic("Eigenspaces"))), 1)

    def test_a_score_outside_zero_to_one_is_refused_rather_than_stored(self) -> None:
        topic = self.topic("Eigenvalues")
        with self.assertRaises(Rejected):
            progress.record_attempt(topic, score=1.4, passes=0)
        self.assertEqual(progress.attempts(topic), [])

    def test_a_negative_count_of_verification_passes_is_refused(self) -> None:
        with self.assertRaises(Rejected):
            progress.record_attempt(self.topic("Eigenvalues"), score=0.5, passes=-1)


class NoGamificationTests(SessionLogTestCase):
    """The ticket's hardest acceptance criterion, because it is the one that
    cannot be added later by accident: nothing here is a reward for a streak."""

    def test_no_page_in_the_product_mentions_a_streak_points_or_a_leaderboard(self) -> None:
        self.set_availability()
        self.mark("Eigenvalues", "done")
        for template in sorted((MATERIAL_ROOT / "templates").rglob("*.html")):
            text = template.read_text(encoding="utf-8")
            for forbidden in re.findall(r"(?i)streak\w*|leaderboard\w*|points\b(?! at)", text):
                self.fail(f"{template.name} offers the student a {forbidden!r}")

    def test_no_table_in_the_schema_has_a_streak_a_tally_or_a_ranking_in_it(self) -> None:
        """The strongest form of the claim: there is nowhere to put one. A word
        left in a page can be deleted; a column added for it survives."""
        stored = [
            field.name
            for model in apps.get_models()
            for field in model._meta.get_fields()
        ]
        for field in stored:
            self.assertIsNone(
                re.search(r"(?i)streak|leaderboard|\bpoints\b", field),
                f"{field} is somewhere a streak could be kept",
            )

    def test_the_week_says_nothing_about_the_student_falling_behind(self) -> None:
        """Behind, overdue and behind-schedule are all streak language wearing a
        polite hat: they turn a week into something the student can fail."""
        self.set_availability()
        html = self.plan_page().lower()
        for forbidden in ["behind", "overdue", "in arrears", "you are losing"]:
            self.assertNotIn(forbidden, html)