"""The schedule: a week of sessions built on the confirmed topic path.

The model is faked at ``material.model.complete`` because topics have to exist
before a schedule can be built on them. Nothing else about the application is
faked: generation is arithmetic on the student's own topics - their order, their
weight, and the time they said they have - so every assertion here is about what
the student sees on the page, never about how the numbers were arrived at.

The clock is pinned in a few tests, and that is not a second seam. A date is an
input a student supplies rather than a collaborator the application reaches for,
so most tests build their exam date relative to today and need nothing pinned.
The rest pin one because "three days a week" only has an answer if the week it
is spread across is a known week; the application above the pinned date is
entirely real, and the tests still go in through HTTP.

The two modes are the point of the ticket, so both are driven here: exam mode
once a date exists, open mode before that. Open mode is what makes the app
worth opening in a week with no exam in sight, and a plan that pretends
otherwise is a plan the student closes.
"""

import re
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any, Iterator
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from material import planning
from material.tests.helpers import (
    LINEAR_ALGEBRA_PDF,
    fake_topic,
    pdf_upload,
    topic_pk,
    use_fake_model,
    use_temporary_media_root,
)


class ScheduleTestCase(TestCase):
    """One course uploaded, its topic path confirmed, and no plan yet."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        self.infer(
            fake_topic("Eigenvalues", [2, 3]),
            fake_topic("Eigenspaces", [4, 5]),
            fake_topic("Diagonalisation", [5]),
        )
        self.client.post("/courses/1/topics/confirm/")

    def infer(self, *topics: Any) -> None:
        with use_fake_model(*topics):
            self.client.post("/courses/1/topics/infer/")

    def confirmed_path(self, *topics: Any) -> None:
        """Work the topic path out and confirm it, in the order the student does."""
        self.infer(*topics)
        self.client.post("/courses/1/topics/confirm/")

    def topic_pk(self, title: str) -> str:
        """The id the topic page carries for a topic, found the way a student reads it."""
        return topic_pk(self.client.get("/courses/1/topics/").content.decode(), title)

    def set_topic_weight(self, title: str, units: int) -> None:
        """Tell a topic how much of the week it is worth, as the edit form does."""
        self.client.post(
            f"/courses/1/topics/{self.topic_pk(title)}/weight/", {"weight": str(units)}
        )

    def set_availability(self, *, days: int, hours: int, exam: str = "") -> Any:
        """Say when the exam is and how much time there is, as the form asks."""
        return self.client.post(
            "/courses/1/plan/settings/",
            {"exam_date": exam, "days_per_week": str(days), "hours_per_week": str(hours)},
        )

    def plan_page(self) -> str:
        response = self.client.get("/courses/1/plan/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def week(self, html: str) -> str:
        """The student's week, as its own piece of the page."""
        assert 'class="week"' in html, "no week shown"
        return html.split('class="week"', 1)[1].split("</ol>", 1)[0]

    def session_titles(self, html: str) -> list[str]:
        """The topics of the sessions in the week, in the order they are shown."""
        return [
            title.strip()
            for title in re.findall(
                r'<h3 class="session-topic">(.*?)</h3>', self.week(html)
            )
        ]

    def session_minutes(self, html: str, title: str) -> int:
        """How long the session on a given topic is, as the student reads it."""
        block = self.session_block(html, title)
        match = re.search(r"(\d+)\s*min", block)
        assert match is not None, f"no length shown for {title!r}"
        return int(match.group(1))

    def session_block(self, html: str, title: str) -> str:
        for block in self.week(html).split('<li class="session"')[1:]:
            if f">{title}</h3>" in block:
                return block
        raise AssertionError(f"no session on {title!r} in the week")

    def session_weekdays(self, html: str) -> list[str]:
        """Which days of the week each session is on, as the week names them."""
        return re.findall(
            r'<p class="session-when">(\w{3}) ', self.week(html)
        )

    def week_minutes(self, html: str) -> int:
        """The time this week's sessions take, as the student reads it off them."""
        return sum(self.session_minutes(html, title) for title in self.session_titles(html))

    def card(self) -> str:
        """The session the student starts now, as its own piece of the page."""
        html = self.plan_page()
        assert 'class="panel next-session"' in html, "no session waiting on the page"
        return html.split('class="panel next-session"', 1)[1].split("</section>", 1)[0]

    def days_from_today(self, days: int) -> str:
        """An exam date a number of days from now, in the form the date input takes."""
        return (timezone.localdate() + timedelta(days=days)).isoformat()


def seven_topics() -> list[Any]:
    """Enough topics that a week cannot hold them all, on a five-slide deck.

    Slides are reused rather than invented: the fixture has five pages, and a
    topic is allowed to share a slide with another.
    """
    return [fake_topic(f"Topic {n}", [(n - 1) % 5 + 1]) for n in range(1, 8)]


@contextmanager
def a_week_of(moment: date) -> Iterator[None]:
    """Build the week as though the student were living on a given day.

    A week only has an answer once you know which week it is: "three days a
    week" is Monday, Wednesday and Friday in one week and something else in the
    next. The date is the one thing a test has to supply; everything above it is
    the real application, reached over HTTP.
    """
    with mock.patch.object(planning, "today", return_value=moment):
        yield


class SettingAvailabilityTests(ScheduleTestCase):
    def test_the_student_is_asked_when_their_exam_is_and_how_much_time_they_have(self) -> None:
        html = self.plan_page()
        self.assertIn("Exam date", html)
        self.assertIn("Days a week you can study", html)
        self.assertIn("Hours a week you can study", html)

    def test_saving_their_availability_builds_a_week_of_sessions(self) -> None:
        self.set_availability(days=3, hours=6)
        self.assertEqual(
            self.session_titles(self.plan_page()),
            ["Eigenvalues", "Eigenspaces", "Diagonalisation"],
        )

    def test_a_week_holds_no_more_sessions_than_the_days_the_study_on(self) -> None:
        self.set_availability(days=2, hours=10)
        self.assertEqual(len(self.session_titles(self.plan_page())), 2)

    def test_the_settings_the_student_gave_are_still_there_next_time(self) -> None:
        self.set_availability(days=3, hours=6)
        html = self.plan_page()
        self.assertEqual(setting(html, "hours_per_week"), "6")
        self.assertEqual(setting(html, "days_per_week"), "3")

    def test_time_the_student_cannot_have_is_refused(self) -> None:
        response = self.set_availability(days=3, hours=0)
        self.assertIn("between one and 24 hours", response.content.decode())

    def test_time_that_is_not_a_number_says_so_rather_than_crashing(self) -> None:
        response = self.client.post(
            "/courses/1/plan/settings/",
            {"exam_date": "", "days_per_week": "3", "hours_per_week": "loads"},
        )
        self.assertIn("whole number", response.content.decode())

    def test_no_days_says_so_rather_than_building_an_empty_week(self) -> None:
        response = self.set_availability(days=0, hours=6)
        self.assertIn("at least one day", response.content.decode())

    def test_a_plan_needs_a_topic_path_the_student_has_confirmed(self) -> None:
        self.client.post("/courses/1/topics/add/", {"title": "Unconfirmed change"})
        response = self.client.get("/courses/1/plan/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Confirm the topic path", response.content.decode())


def setting(html: str, name: str) -> str:
    """What the form has remembered for one of the settings the student gave."""
    for tag in re.findall(r"<input[^>]*>", html):
        if f'name="{name}"' in tag:
            return re.search(r'value="([^"]*)"', tag).group(1)  # type: ignore[union-attr]
    raise AssertionError(f"the form has no field named {name!r}")


class WeightedSessionTests(ScheduleTestCase):
    def test_a_topic_with_more_material_behind_it_gets_a_longer_session(self) -> None:
        self.confirmed_path(fake_topic("A digression", [5]), fake_topic("The whole chapter", [2, 3, 4, 5]))
        self.set_availability(days=2, hours=4)
        html = self.plan_page()
        self.assertGreater(
            self.session_minutes(html, "The whole chapter"),
            self.session_minutes(html, "A digression"),
        )

    def test_the_time_the_student_gives_a_topic_is_the_time_its_session_takes(self) -> None:
        self.set_topic_weight("Eigenvalues", 20)
        self.client.post("/courses/1/topics/confirm/")
        self.set_availability(days=3, hours=6)
        html = self.plan_page()
        lengths = {
            title: self.session_minutes(html, title)
            for title in self.session_titles(html)
        }
        self.assertEqual(max(lengths, key=lambda title: lengths[title]), "Eigenvalues")

    def test_a_smaller_topic_is_never_given_a_longer_session_than_a_bigger_one(self) -> None:
        self.confirmed_path(
            fake_topic("Barely a topic", [5]),
            fake_topic("A topic", [4]),
            fake_topic("A long chapter", [2, 3, 4, 5]),
        )
        self.set_availability(days=3, hours=6)
        html = self.plan_page()
        lengths = {
            title: self.session_minutes(html, title) for title in self.session_titles(html)
        }
        self.assertLessEqual(lengths["Barely a topic"], lengths["A topic"])
        self.assertLessEqual(lengths["A topic"], lengths["A long chapter"])

    def test_the_week_of_sessions_fits_the_hours_the_student_said(self) -> None:
        self.set_availability(days=3, hours=2)
        html = self.plan_page()
        self.assertLessEqual(self.week_minutes(html), 2 * 60)

    def test_no_session_is_shorter_than_a_sitting_down_to_study(self) -> None:
        self.confirmed_path(*[fake_topic(f"Topic {n}", [n]) for n in range(1, 6)])
        self.set_availability(days=5, hours=1)
        html = self.plan_page()
        for title in self.session_titles(html):
            self.assertGreaterEqual(self.session_minutes(html, title), 30)

    def test_no_topic_is_promised_one_sitting_longer_than_its_worth(self) -> None:
        self.confirmed_path(fake_topic("Everything", [2, 3, 4, 5]))
        self.set_availability(days=1, hours=20)
        html = self.plan_page()
        self.assertLessEqual(self.session_minutes(html, "Everything"), 180)


#: A Monday with a known week after it, so "three days a week" lands on days a
#: test can name: Monday, Wednesday, Friday.
A_MONDAY = date(2026, 11, 2)


class OpenModeTests(ScheduleTestCase):
    def test_no_exam_date_leaves_the_student_in_open_mode(self) -> None:
        self.set_availability(days=3, hours=6)
        self.assertIn("Open mode", self.plan_page())

    def test_open_mode_keeps_the_order_the_student_confirmed(self) -> None:
        self.client.post(
            f"/courses/1/topics/{self.topic_pk('Diagonalisation')}/move/",
            {"direction": "up"},
        )
        self.client.post("/courses/1/topics/confirm/")
        self.set_availability(days=3, hours=6)
        self.assertEqual(
            self.session_titles(self.plan_page()),
            ["Eigenvalues", "Diagonalisation", "Eigenspaces"],
        )

    def test_open_mode_does_not_count_down_to_a_date_the_student_has_not_got(self) -> None:
        self.set_availability(days=3, hours=6)
        html = self.plan_page()
        self.assertNotIn("to go", html)
        self.assertNotIn("Exam on", html)

    def test_the_plan_still_arrives_with_no_exam_date_at_all(self) -> None:
        self.set_availability(days=3, hours=6)
        self.assertEqual(self.session_titles(self.plan_page())[0], "Eigenvalues")

    def test_open_mode_never_says_a_topic_does_not_fit_before_an_exam(self) -> None:
        """Open mode has no exam, so it has no deadline to fail to meet."""
        self.confirmed_path(*seven_topics())
        self.set_availability(days=1, hours=2)
        self.assertNotIn("before your exam", self.plan_page())

    def test_every_topic_gets_a_session_however_far_apart_the_days_land(self) -> None:
        """A day a week and more topics than weeks is not a reason to drop any."""
        self.confirmed_path(*seven_topics())
        self.set_availability(days=1, hours=2)
        html = self.plan_page()
        for title in [f"Topic {n}" for n in range(1, 8)]:
            self.assertIn(f'class="session-topic">{title}<', html)


class StudyDaysTests(ScheduleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.confirmed_path(*seven_topics())

    def test_three_days_a_week_is_spread_across_the_week_not_clumped(self) -> None:
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam="2026-11-20")
            self.assertEqual(
                self.session_weekdays(self.plan_page())[:3], ["Mon", "Wed", "Fri"]
            )

    def test_five_days_a_week_is_the_working_week(self) -> None:
        with a_week_of(A_MONDAY):
            self.set_availability(days=5, hours=10, exam="2026-11-20")
            self.assertEqual(
                self.session_weekdays(self.plan_page()),
                ["Mon", "Tue", "Wed", "Thu", "Fri"],
            )

    def test_one_day_a_week_is_a_single_day_of_the_week(self) -> None:
        with a_week_of(A_MONDAY):
            self.set_availability(days=1, hours=6, exam="2026-11-20")
            self.assertEqual(self.session_weekdays(self.plan_page())[0], "Mon")

    def test_six_days_a_week_is_the_working_week_and_saturday(self) -> None:
        with a_week_of(A_MONDAY):
            self.set_availability(days=6, hours=20, exam="2026-11-20")
            weekdays = self.session_weekdays(self.plan_page())
        self.assertEqual(weekdays, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])


class ExamModeTests(ScheduleTestCase):
    def test_entering_an_exam_date_switches_the_student_into_exam_mode(self) -> None:
        self.set_availability(days=3, hours=6)
        self.assertNotIn("Exam mode", self.plan_page())
        self.set_availability(days=3, hours=6, exam=self.days_from_today(40))
        self.assertIn("Exam mode", self.plan_page())

    def test_exam_mode_says_how_long_the_student_has_left(self) -> None:
        self.set_availability(days=3, hours=6, exam=self.days_from_today(10))
        self.assertIn("10 days to go", self.plan_page())

    def test_an_exam_a_day_away_says_it_is_tomorrow(self) -> None:
        self.set_availability(days=3, hours=6, exam=self.days_from_today(1))
        self.assertIn("1 day to go", self.plan_page())

    def test_an_exam_date_that_has_gone_is_said_rather_than_counted_down(self) -> None:
        self.set_availability(days=3, hours=6, exam=self.days_from_today(-5))
        html = self.plan_page()
        self.assertIn("Your exam date has passed", html)
        self.assertNotIn("days to go", html)

    def test_an_exam_today_leaves_nothing_to_revise_and_says_so(self) -> None:
        self.set_availability(days=3, hours=6, exam=self.days_from_today(0))
        html = self.plan_page()
        self.assertIn("It is your exam today", html)
        self.assertIn("no revision left to schedule", html)
        self.assertNotIn("Every date on this plan has passed", html)

    def test_nothing_is_scheduled_on_the_day_of_the_exam_or_after_it(self) -> None:
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam="2026-11-06")
            week = self.week(self.plan_page())
        self.assertIn("Mon 2 Nov", week)
        self.assertIn("Wed 4 Nov", week)
        self.assertNotIn("6 Nov", week)

    def test_an_exam_too_close_for_the_whole_path_says_which_topics_do_not_fit(self) -> None:
        with a_week_of(A_MONDAY):
            self.set_availability(days=1, hours=6, exam="2026-11-03")
            html = self.plan_page()
        self.assertEqual(self.session_titles(html), ["Eigenvalues"])
        behind = html.split("Not before your exam")[1].split("</section>")[0]
        self.assertIn("Eigenspaces", behind)
        self.assertIn("Diagonalisation", behind)

    def test_clearing_the_exam_date_puts_the_student_back_in_open_mode(self) -> None:
        self.set_availability(days=3, hours=6, exam=self.days_from_today(40))
        self.set_availability(days=3, hours=6)
        html = self.plan_page()
        self.assertIn("Open mode", html)
        self.assertEqual(setting(html, "exam_date"), "")

    def test_more_days_than_a_week_has_is_refused(self) -> None:
        response = self.set_availability(days=8, hours=6)
        self.assertIn("no more than 7", response.content.decode())

    def test_more_hours_than_a_person_has_is_refused(self) -> None:
        response = self.set_availability(days=3, hours=200)
        self.assertIn("168 hours", response.content.decode())

    def test_an_exam_date_that_cannot_be_read_is_refused_rather_than_guessed(self) -> None:
        response = self.set_availability(days=3, hours=6, exam="next tuesday")
        self.assertIn("Give the exam date as a day", response.content.decode())


class TodaysSessionTests(ScheduleTestCase):
    def test_the_session_the_student_starts_today_is_on_the_page_already(self) -> None:
        self.set_availability(days=7, hours=6)
        self.assertIn("Today's session", self.plan_page())

    def test_starting_a_session_is_one_tap_from_the_slides_it_came_from(self) -> None:
        self.set_availability(days=7, hours=6)
        card = self.card()
        self.assertIn('href="/courses/1/read/#1-p2"', card)
        self.assertIn("Start this session", card)

    def test_the_card_names_the_topic_and_how_long_it_is(self) -> None:
        self.set_availability(days=7, hours=6)
        card = self.card()
        self.assertIn("Eigenvalues", card)
        self.assertRegex(card, r"\d+ min")

    def test_on_a_day_the_student_does_not_study_the_card_offers_the_next_one(self) -> None:
        with a_week_of(date(2026, 11, 3)):  # a Tuesday
            self.set_availability(days=1, hours=6)  # Mondays only
            card = self.card()
        self.assertIn("Next session", card)
        self.assertIn("Monday 9 November", card)

    def test_a_topic_with_no_slides_does_not_offer_a_link_to_nothing(self) -> None:
        self.client.post("/courses/1/topics/add/", {"title": "From the lecturer"})
        self.client.post("/courses/1/topics/confirm/")
        self.set_availability(days=7, hours=6)
        html = self.plan_page()
        self.assertNotIn('read/#"', html)
        self.assertIn("No slides pointed at yet", html)

    def test_a_week_whose_dates_have_all_gone_offers_a_fresh_one(self) -> None:
        self.set_availability(days=7, hours=6)
        with a_week_of(date(2026, 11, 10)):
            html = self.plan_page()
        self.assertIn("Every date on this plan has passed", html)


class PlanNavigationTests(ScheduleTestCase):
    def test_the_week_is_reachable_from_the_topic_path(self) -> None:
        html = self.client.get("/courses/1/topics/").content.decode()
        self.assertIn('href="/courses/1/plan/"', html)

    def test_the_week_is_reachable_from_the_material(self) -> None:
        html = self.client.get("/courses/1/read/").content.decode()
        self.assertIn('href="/courses/1/plan/"', html)

    def test_the_week_is_reachable_from_the_list_of_courses(self) -> None:
        html = self.client.get("/").content.decode()
        self.assertIn('href="/courses/1/plan/"', html)

    def test_changing_the_topic_path_puts_the_week_away_until_it_is_confirmed(self) -> None:
        self.set_availability(days=3, hours=6)
        self.client.post("/courses/1/topics/add/", {"title": "Late addition"})
        self.assertEqual(self.client.get("/courses/1/plan/").status_code, 409)
        self.client.post("/courses/1/topics/confirm/")
        self.assertEqual(self.client.get("/courses/1/plan/").status_code, 200)

    def test_a_week_never_shows_another_course_s_topics(self) -> None:
        self.client.post("/courses/new/", {"title": "Other", "files": [pdf_upload()]})
        with use_fake_model(fake_topic("Titration curves", [2])):
            self.client.post("/courses/2/topics/infer/")
        self.client.post("/courses/2/topics/confirm/")
        self.client.post(
            "/courses/2/plan/settings/",
            {"exam_date": "", "days_per_week": "3", "hours_per_week": "6"},
        )
        html = self.client.get("/courses/2/plan/").content.decode()
        self.assertIn("Titration curves", html)
        self.assertNotIn("Eigenvalues", html)


class OneTopicOneSessionTests(ScheduleTestCase):
    def test_a_topic_is_one_session_and_is_never_split_across_days(self) -> None:
        self.confirmed_path(fake_topic("Whole chapter", [2, 3, 4, 5]))
        self.set_availability(days=3, hours=6)
        html = self.plan_page()
        self.assertEqual(self.session_titles(html), ["Whole chapter"])

    def test_a_topic_the_student_split_earlier_gets_a_session_each(self) -> None:
        self.confirmed_path(fake_topic("Whole chapter", [2, 3, 4, 5]))
        self.client.post(
            f"/courses/1/topics/{self.topic_pk('Whole chapter')}/split/",
            {"slide": "3", "title": "Eigenspaces"},
        )
        self.client.post("/courses/1/topics/confirm/")
        self.set_availability(days=3, hours=6)
        self.assertEqual(
            self.session_titles(self.plan_page()), ["Whole chapter", "Eigenspaces"]
        )


class SessionCountTests(ScheduleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.confirmed_path(
            fake_topic("Eigenvalues", [2]),
            fake_topic("Characteristic polynomials", [3]),
            fake_topic("Eigenspaces", [4]),
            fake_topic("Diagonalisation", [5]),
            fake_topic("Repeated roots", [5]),
            fake_topic("Symmetric matrices", [4, 5]),
        )

    def test_a_budget_too_tight_for_a_session_a_day_offers_fewer(self) -> None:
        self.set_availability(days=5, hours=1)
        self.assertEqual(len(self.session_titles(self.plan_page())), 2)

    def test_a_budget_that_stretches_offers_one_session_for_each_study_day(self) -> None:
        self.set_availability(days=5, hours=10)
        self.assertEqual(len(self.session_titles(self.plan_page())), 5)

    def test_the_days_the_student_studies_still_cap_the_sessions_offered(self) -> None:
        self.set_availability(days=2, hours=20)
        self.assertEqual(len(self.session_titles(self.plan_page())), 2)

    def test_the_student_can_see_the_weeks_after_this_one_too(self) -> None:
        self.set_availability(days=3, hours=6)
        self.assertIn("Week 2", self.plan_page())

    def test_every_topic_of_the_course_gets_a_session_somewhere(self) -> None:
        self.set_availability(days=3, hours=6)
        html = self.plan_page()
        for title in [
            "Eigenvalues",
            "Characteristic polynomials",
            "Eigenspaces",
            "Diagonalisation",
            "Repeated roots",
            "Symmetric matrices",
        ]:
            self.assertIn(f'class="session-topic">{title}<', html)
