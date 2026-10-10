"""Falling behind: missed sessions shift later, the finish date is told straight,
and the student chooses what to drop.

The rule this file exists to pin down is one sentence: **missed sessions shift
later and never compress**. Everything else here - the finish date, the cost of
a cut - is that rule made visible. An implementation that quietly fits the same
work into fewer days produces a plan the student already knows is impossible,
and that is how they stop opening the app.

So the tests drive redistribution through HTTP, exactly as a student does:
build the week, let the days go by, press "build this week again", read the
page. Nothing here reaches past the HTTP seam for the dates - they are read out
of the page the way the student reads them - because a date asserted by query
is a date the student cannot check.

The clock is pinned to a known Monday, for the same reason it is in
``test_schedule``: "three days a week" only has an answer once you know which
week it is. Above the pinned date the application is entirely real.
"""

import re
from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator
from unittest import mock

from django.test import TestCase

from material import planning
from material.models import Topic
from material.tests.helpers import (
    LINEAR_ALGEBRA_PDF,
    fake_topic,
    pdf_upload,
    topic_pk,
    use_fake_model,
    use_temporary_media_root,
)

#: A Monday in a known week, so every day the plan lands on can be said out loud.
A_MONDAY = date(2026, 11, 2)

#: Two weeks later, still a Monday: the day a student comes back to the app
#: having spent the fortnight doing nothing at all.
A_FORTNIGHT_LATER = date(2026, 11, 16)

#: The exam the student told us about: a Friday five weeks in.
THE_EXAM = "2026-11-27"


def topic_names(count: int) -> list[Any]:
    """A path long enough that a week cannot hold it, on a five-slide deck.

    Slides are reused rather than invented: the fixture has five pages and a
    topic is allowed to share a slide with another. The consequence is that the
    inferred path comes back in order of each topic's first slide rather than
    in the order these claims were written, so nothing in these tests asserts
    on the position of a topic by name - they read what the page shows.
    """
    return [fake_topic(f"Topic {n}", [(n - 1) % 5 + 1]) for n in range(1, count + 1)]


@contextmanager
def a_week_of(moment: date) -> Iterator[None]:
    """Build and read the week as though the student were living on a given day."""
    with mock.patch.object(planning, "today", return_value=moment):
        yield


def flat(text: str) -> str:
    """The page with its line wrapping squeezed out.

    Where a template happens to break a line is not something a student can
    see and must not be something a test asserts on.
    """
    return " ".join(text.split())


class BehindScheduleTestCase(TestCase):
    """One course uploaded, a confirmed topic path, and the days going by."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"files": [pdf_upload(LINEAR_ALGEBRA_PDF)]})
        self.confirm_path(*topic_names(8))

    def confirm_path(self, *topics: Any) -> None:
        """Work the topic path out and confirm it, the way the student does."""
        with use_fake_model(*topics):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/confirm/")

    def set_availability(self, *, days: int = 3, hours: int = 6, exam: str = "") -> Any:
        """Say when the exam is and how much time there is, as the form asks."""
        return self.client.post(
            "/courses/1/plan/settings/",
            {"exam_date": exam, "days_per_week": str(days), "hours_per_week": str(hours)},
        )

    def rebuild(self, *, days: int = 3, hours: int = 6, exam: str = THE_EXAM) -> Any:
        """Press the button that builds the week again, keeping the same week."""
        return self.set_availability(days=days, hours=hours, exam=exam)

    def plan_page(self) -> str:
        response = self.client.get("/courses/1/plan/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # -- reading the page the way the student reads it -----------------------

    def session_dates(self, html: str) -> list[str]:
        """Every day the plan puts a session on, in the order the page shows them.

        Read as rendered dates rather than as ``data-`` attributes: this is what
        the student is shown, and a test that asserts on an implementation
        detail of the template is asserting on something that can change
        without the student ever finding out.
        """
        found = []
        for block in html.split('<li class="session')[1:]:
            when = re.search(r'<p class="session-when">\s*(\w{3} \d+ \w{3})', block)
            assert when is not None, f"a session shows no day: {block}"
            found.append(when.group(1))
        return found

    def session_titles(self, html: str) -> list[str]:
        return [
            title.strip()
            for title in re.findall(r'<h3 class="session-topic">(.*?)</h3>', html)
        ]

    def session_minutes(self, html: str, topic: str) -> int:
        """How long the session on a topic is, as the student reads it."""
        for block in html.split('<li class="session"')[1:]:
            if f">{topic}</h3>" in block:
                found = re.search(r'<p class="session-length">(\d+) min</p>', block)
                assert found is not None, f"no length shown for {topic!r}"
                return int(found.group(1))
        raise AssertionError(f"no session on {topic!r} in the plan")

    def behind(self) -> str:
        """A fortnight of doing nothing later on, which is the state the behind
        panel exists for."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild()
            return self.plan_page()

    def completion(self, html: str) -> str:
        found = re.search(r'<section class="panel completion">(.*?)</section>', html, re.S)
        assert found is not None, "no completion panel on the plan page"
        return flat(found.group(1))

    def log_entries(self, html: str) -> list[str]:
        found = re.search(r'<section class="panel session-log">(.*?)</section>', html, re.S)
        assert found is not None, "no session log on the plan page"
        return [
            flat(re.search(r'<p class="log-topic">(.*?)</p>', block, re.S).group(1))  # type: ignore[union-attr]
            for block in found.group(1).split('<li class="log-entry"')[1:]
        ]

    def week_session_counts(self, html: str) -> list[int]:
        """How many sessions each week of the plan holds, in the order shown."""
        return [
            len(re.findall(r'<h3 class="session-topic">', week))
            for week in re.findall(r'<ol class="week">(.*?)</ol>', html, re.S)
        ]

    def fullest_week_minutes(self, html: str) -> int:
        """The most time any one week of the plan takes on."""
        return max(
            (
                sum(
                    int(minutes)
                    for minutes in re.findall(
                        r'<p class="session-length">(\d+) min</p>', week
                    )
                )
                for week in re.findall(r'<ol class="week">(.*?)</ol>', html, re.S)
            ),
            default=0,
        )

    def topic_id(self, title: str) -> str:
        """The id a topic carries, read off the page the student would click from."""
        return topic_pk(self.client.get("/courses/1/topics/").content.decode(), title)

    def topic(self, title: str) -> Any:
        return Topic.objects.get(title=title)

    def behind_panel(self, html: str) -> str:
        found = re.search(r'<section class="panel behind">(.*?)</section>', html, re.S)
        assert found is not None, "nothing on the page says the student is behind"
        return flat(found.group(1))

    def projected_on(self, html: str) -> str:
        """The day the page says the student will actually finish, in words."""
        found = re.search(
            r'<strong class="projected-on">(.*?)</strong>', self.behind_panel(html), re.S
        )
        assert found is not None, "no finish date shown"
        return flat(found.group(1))

    def cut_costs(self, html: str) -> dict[str, str]:
        """What the page says each topic costs to cut, keyed the way the student reads it."""
        found = re.search(r'<ul class="cut-list">(.*?)</ul>', self.behind_panel(html), re.S)
        assert found is not None, "no topics offered to cut"
        costs = {}
        for block in found.group(1).split('<li class="cut-option"')[1:]:
            title = re.search(r'<p class="cut-topic">(.*?)</p>', block, re.S)
            cost = re.search(r'<p class="cut-cost">(.*?)</p>', block, re.S)
            assert title and cost, f"a cut option is missing part of itself: {block}"
            costs[flat(title.group(1))] = flat(cost.group(1))
        return costs

    def mark_url(self, html: str, topic: str) -> str:
        """The action that marks one session done, found the way a click finds it."""
        for block in html.split('<li class="session"')[1:]:
            if f">{topic}</h3>" in block:
                found = re.search(r'action="([^"]+)"', block)
                assert found is not None, f"no button offered for {topic!r}"
                return found.group(1)
        raise AssertionError(f"no session on {topic!r} in the plan")

    def mark(self, html: str, topic: str) -> None:
        self.client.post(self.mark_url(html, topic), {"state": "done"})

    def cut_url(self, html: str, topic: str) -> str:
        """The action that cuts one topic, found the way a click would find it."""
        # Split on the class without its closing quote: the topics already cut
        # carry a second class and would otherwise be swallowed by the option
        # before them, which is how "put this back" ends up pressing "cut".
        # Both lists are searched because a cut stays undoable long after the
        # student is no longer behind.
        blocks = [
            block
            for chunk in (self.behind_panel(html), html)
            for block in chunk.split('class="cut-option')[1:]
        ]
        for block in blocks:
            if f">{topic}</p>" in block:
                found = re.search(r'action="([^"]+)"', block)
                assert found is not None, f"no cut button offered for {topic!r}"
                return found.group(1)
        raise AssertionError(f"{topic!r} is not offered as something to cut")


class MissedSessionsShiftTests(BehindScheduleTestCase):
    def test_a_missed_session_lands_on_a_later_day_rather_than_joining_another(self) -> None:
        """The rule the whole ticket rests on, read off the page.

        Eight topics on three days a week. Nothing gets done, a fortnight goes
        by, and the student presses the same button. Every session must be on a
        day of its own: a session added to a day that already has one is work
        compressed into a day the student already promised themselves.
        """
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6)
            self.assertEqual(len(self.session_dates(self.plan_page())), 8)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild(exam="")
            dates = self.session_dates(self.plan_page())
        self.assertEqual(len(dates), 8)
        self.assertEqual(len(set(dates)), 8, f"two sessions share a day: {dates}")

    def test_the_same_work_still_takes_the_same_number_of_weeks_after_the_missed_days(self) -> None:
        """Eight sessions at three a week is three weeks, before and after.

        Compressing them into the weeks the student has left would be the
        failure this ticket is about, so the assertion is on the whole span:
        the first session on the Monday they came back, and the last still a
        fortnight after that rather than packed into the days they had.
        """
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6)
            first = self.session_dates(self.plan_page())
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild(exam="")
            shifted = self.session_dates(self.plan_page())
        self.assertEqual(
            first,
            ["Mon 2 Nov", "Wed 4 Nov", "Fri 6 Nov", "Mon 9 Nov", "Wed 11 Nov",
             "Fri 13 Nov", "Mon 16 Nov", "Wed 18 Nov"],
        )
        self.assertEqual(
            shifted,
            ["Mon 16 Nov", "Wed 18 Nov", "Fri 20 Nov", "Mon 23 Nov", "Wed 25 Nov",
             "Fri 27 Nov", "Mon 30 Nov", "Wed 2 Dec"],
        )

    def test_no_week_holds_more_sessions_than_the_days_the_students_told_us_they_study(self) -> None:
        """Three days a week stays three days a week however far behind they are."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild()
            html = self.plan_page()
        weeks = re.findall(r'<ol class="week">(.*?)</ol>', html, re.S)
        self.assertEqual(len(weeks), 2, "the rebuild produced no weeks to check")
        for week in weeks:
            self.assertLessEqual(len(re.findall(r'<h3 class="session-topic">', week)), 3)

    def test_a_session_the_student_finished_keeps_the_day_it_was_on(self) -> None:
        """Work already done does not get moved forward to a day that has not happened.

        The record lives on the topic and the session stays where the student
        met it, which is the difference between a plan the student can trust
        and one that quietly re-books their finished work.
        """
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
            html = self.plan_page()
            self.mark(html, "Topic 1")
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild()
            html = self.plan_page()
        self.assertIn("Mon 2 Nov", self.session_dates(html))
        self.assertEqual(self.session_titles(html)[0], "Topic 1")


class ProjectedFinishTests(BehindScheduleTestCase):
    def test_a_student_on_track_is_not_told_they_are_behind(self) -> None:
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
            html = self.plan_page()
        self.assertNotIn('class="panel behind"', html)

    def test_behind_schedule_the_student_is_told_the_day_they_will_actually_finish(self) -> None:
        """Eight topics, three days a week, a fortnight spent doing nothing.

        The eight remaining sessions run 16, 18, 20, 23, 25, 27, 30 November
        and 2 December. Only five of them fit before the exam on the 27th, so
        the honest answer is the 2nd - not a plan that squeezes the last three
        into the week the student already has.
        """
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild()
            html = self.plan_page()
        self.assertEqual(self.projected_on(html), "Wednesday 2 December")

    def test_the_projected_finish_is_what_the_remaining_work_actually_takes(self) -> None:
        """The date is read off the pace the student set, not off the exam.

        Eight sessions still to do at three a week from a Monday is the 2nd:
        five in the first fortnight, three in the next. A date that matched the
        exam instead would be the plan lying about how far along they are.
        """
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild()
            html = self.plan_page()
        self.assertEqual(self.projected_on(html), "Wednesday 2 December")
        self.assertIn("8 sessions still to do", self.behind_panel(html))

    def test_the_page_says_how_late_the_projected_finish_is(self) -> None:
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild()
            panel = self.behind_panel(self.plan_page())
        self.assertIn("5 days after your exam", panel)
        self.assertIn("27 November 2026", panel)

    def test_work_the_student_has_done_is_not_counted_against_them(self) -> None:
        """Finishing two sessions in the first fortnight is real progress and
        has to move the finish date, or the projection is measuring the course
        rather than the work left. Six sessions left is the 27th; eight was
        the 2nd of December."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam="2026-11-20")
            html = self.plan_page()
            for done in ("Topic 1", "Topic 6"):
                self.mark(html, done)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild(exam="2026-11-20")
            html = self.plan_page()
        self.assertEqual(self.projected_on(html), "Friday 27 November")
        self.assertIn("6 sessions still to do", self.behind_panel(html))

    def test_an_open_plan_has_no_deadline_to_be_behind(self) -> None:
        """With no exam there is nothing to be late against, so the page does
        not invent one."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6)
            self.assertNotIn('class="panel behind"', self.plan_page())


class CuttingTopicsTests(BehindScheduleTestCase):
    def test_the_student_is_offered_the_topics_the_week_is_spending_on(self) -> None:
        """Only a topic with a session is costing them anything. Offering to cut
        one the plan never scheduled would be offering a button whose number
        does not move when it is pressed."""
        html = self.behind()
        self.assertEqual(set(self.cut_costs(html)), set(self.session_titles(html)))

    def test_a_topic_the_plan_never_scheduled_is_not_offered_as_a_cut(self) -> None:
        html = self.behind()
        self.assertIn("Not before your exam", html)
        self.assertNotIn("Topic 5", self.cut_costs(html))

    def test_cutting_a_topic_says_what_it_costs(self) -> None:
        """The cost is the time the topic actually takes and a finish date, in
        the units the student thinks in. A number they cannot act on is
        decoration."""
        costs = self.cut_costs(self.behind())
        self.assertEqual(
            costs["Topic 1"],
            "70 min a week, and you would finish on Monday 30 November, which "
            "is still after it — one cut on its own is not enough",
        )

    def test_the_price_of_a_topic_is_the_length_of_the_session_it_has(self) -> None:
        """The panel quotes the row the plan already holds rather than working
        the share out a second way, so the two cannot drift apart and the
        student is never quoted a minute the plan does not spend."""
        html = self.behind()
        costs = self.cut_costs(html)
        for title, cost in costs.items():
            shown = int(cost.split(" min")[0])
            self.assertEqual(shown, self.session_minutes(html, title), title)

    def test_every_topic_offers_the_same_finish_date_because_any_one_of_them_frees_one_session(self) -> None:
        costs = self.cut_costs(self.behind())
        dates = {cost.split("you would finish on ")[1].split(",")[0] for cost in costs.values()}
        self.assertEqual(dates, {"Monday 30 November"})

    def test_cutting_a_topic_takes_its_session_off_the_plan(self) -> None:
        """The day it had goes to the topic that had none, which is what
        cutting is for: the course does not shrink, the week gets to cover
        one more of it."""
        html = self.behind()
        before = self.session_titles(html)
        with a_week_of(A_FORTNIGHT_LATER):
            self.client.post(self.cut_url(html, "Topic 1"))
            after = self.plan_page()
        titles = self.session_titles(after)
        self.assertNotIn("Topic 1", titles)
        self.assertEqual(len(titles), len(before))
        gained = [title for title in titles if title not in before]
        self.assertEqual(len(gained), 1, f"the freed day did not go anywhere: {gained}")

    def test_cutting_a_topic_moves_the_finish_date_earlier(self) -> None:
        html = self.behind()
        with a_week_of(A_FORTNIGHT_LATER):
            self.client.post(self.cut_url(html, "Topic 1"))
            after = self.plan_page()
        self.assertEqual(self.projected_on(after), "Monday 30 November")

    def test_cutting_a_topic_takes_it_out_of_what_is_still_waiting_to_be_cut(self) -> None:
        html = self.behind()
        with a_week_of(A_FORTNIGHT_LATER):
            self.client.post(self.cut_url(html, "Topic 1"))
            after = self.plan_page()
        self.assertNotIn("Topic 1", self.cut_costs(after))

    def test_cutting_one_that_is_already_cut_puts_it_back(self) -> None:
        """One control per answer, like every other mark on this page: the
        student can undo a cut without hunting for an undo."""
        html = self.behind()
        with a_week_of(A_FORTNIGHT_LATER):
            self.client.post(self.cut_url(html, "Topic 1"))
            after_cut = self.plan_page()
            self.assertIn("Put this back", after_cut)
            self.client.post(self.cut_url(after_cut, "Topic 1"))
            after = self.plan_page()
        self.assertIn("Topic 1", self.session_titles(after))
        self.assertEqual(self.projected_on(after), "Wednesday 2 December")

    def test_a_cut_topic_is_still_on_the_path_the_student_wrote(self) -> None:
        """A cut is a change to the week, not a deletion from the course.

        The student chose what to stop revising, not to forget what the course
        taught. Taking the topic off their path would throw away the order they
        set and the slides they pointed at.
        """
        html = self.behind()
        with a_week_of(A_FORTNIGHT_LATER):
            self.client.post(self.cut_url(html, "Topic 3"))
            path = self.client.get("/courses/1/topics/").content.decode()
        self.assertIn("Topic 3", path)

    def test_a_cut_topic_cannot_be_marked_done_because_it_has_no_session(self) -> None:
        html = self.behind()
        with a_week_of(A_FORTNIGHT_LATER):
            self.client.post(self.cut_url(html, "Topic 3"))
            after = self.plan_page()
        self.assertNotIn("Topic 3", self.session_titles(after))

class NoTwoSessionsOnOneDayTests(BehindScheduleTestCase):
    """The compression the ticket forbids, checked at the one moment it could
    sneak in: a rebuild on a day the student has already worked on."""

    def test_a_rebuild_on_the_day_a_session_was_finished_does_not_stack_two_sessions(self) -> None:
        """Marking the first session done and then pressing the same button
        again used to put the second session on the same Monday. A rebuild is
        exactly where "never compress" would be lost by accident."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
            self.mark(self.plan_page(), "Topic 1")
            self.rebuild()
            dates = self.session_dates(self.plan_page())
        self.assertEqual(len(dates), len(set(dates)), f"two sessions share a day: {dates}")
        self.assertNotIn("Mon 2 Nov", dates[1:])

    def test_a_week_already_worked_in_is_not_handed_out_at_the_students_full_weekly_budget(self) -> None:
        """An hour a week is two short sessions. Finishing one of them this
        morning means the rest of that week carries one short session, not two.

        The floor is the floor: a session is never cut below the shortest
        sitting worth offering, so the remainder of the week can exceed what is
        left of the budget rather than becoming a sitting of nothing. What must
        not happen is the week being handed out whole a second time.
        """
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=1, exam=THE_EXAM)
            self.mark(self.plan_page(), "Topic 1")
            self.rebuild(hours=1)
            html = self.plan_page()
        self.assertEqual(self.fullest_week_minutes(html), 60)
        self.assertEqual(self.week_session_counts(html)[0], 2)


class TheRecordSurvivesEverythingTests(BehindScheduleTestCase):
    """A cut is a change to the week. It cannot reach what the student has
    already finished, and it cannot reach a topic the plan never scheduled."""

    def test_a_topic_already_in_the_record_cannot_be_cut(self) -> None:
        """The plan page never offers this, but the endpoint is one POST away
        and a finished session must not be removable from under the student."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
            self.mark(self.plan_page(), "Topic 1")
            response = self.client.post(
                f"/courses/1/plan/topics/{self.topic_id('Topic 1')}/cut/"
            )
        self.assertIn("already in your record", response.content.decode())
        self.assertIsNotNone(self.topic("Topic 1").completed_on)

    def test_a_cut_leaves_the_share_the_student_had_earned(self) -> None:
        """The share counts the sessions on the week, so removing a finished
        one behind the planner's back would quietly take credit off work the
        student actually did."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
            self.mark(self.plan_page(), "Topic 1")
            self.client.post(f"/courses/1/plan/topics/{self.topic_id('Topic 1')}/cut/")
            html = self.plan_page()
        self.assertIn("You have done 1 of 8 sessions", self.completion(html))
        self.assertEqual(self.log_entries(html), ["Topic 1"])


class OneSessionShortTests(BehindScheduleTestCase):
    def test_a_student_one_session_short_is_told_rather_than_left_to_count(self) -> None:
        """Studying Mondays and Fridays with an exam on the Friday: the exam
        day is not a study day, so the fourth session has no home. The
        projection lands on the exam itself, which is not a plan, and saying so
        is the whole point of the panel."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=2, hours=6, exam=THE_EXAM)
            html = self.plan_page()
            for title in ("Topic 1", "Topic 6", "Topic 2", "Topic 7"):
                self.mark(html, title)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild(days=2)
            html = self.plan_page()
        panel = self.behind_panel(html)
        self.assertEqual(self.projected_on(html), "Friday 27 November")
        self.assertIn("4 sessions still to do", panel)
        self.assertIn("too late to be a plan", panel)

    def test_cutting_one_topic_says_when_one_cut_is_enough(self) -> None:
        """The same panel, the other way round. Three sessions left is three
        study days before the exam, so one cut does get there, and the page has
        to say so rather than hedging."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=2, hours=6, exam=THE_EXAM)
            html = self.plan_page()
            for title in ("Topic 1", "Topic 6", "Topic 2", "Topic 7"):
                self.mark(html, title)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild(days=2)
            costs = self.cut_costs(self.plan_page())
        self.assertTrue(costs)
        for cost in costs.values():
            self.assertIn("before your exam", cost)


class UndoIsAlwaysReachableTests(BehindScheduleTestCase):
    def test_a_cut_can_be_undone_once_the_student_is_no_longer_behind(self) -> None:
        """The student who is on top of it again is not the one who needs the
        undo, but they are the one who will have used it."""
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild()
            html = self.behind()
            self.client.post(self.cut_url(html, "Topic 1"))
            after = self.plan_page()
            self.assertIn("Put this back", after)
            self.set_availability(days=3, hours=6)  # no exam: nothing to be behind
            after = self.plan_page()
        self.assertNotIn('class="panel behind"', after)
        self.assertIn("Put this back", after)
        self.assertIn("Off your week since", after)

    def test_cutting_the_last_topic_still_leaves_a_way_back(self) -> None:
        with a_week_of(A_MONDAY):
            self.set_availability(days=3, hours=6, exam=THE_EXAM)
        with a_week_of(A_FORTNIGHT_LATER):
            self.rebuild()
            html = self.behind()
            for block in self.behind_panel(html).split('class="cut-option')[1:]:
                url = re.search(r'action="([^"]+)"', block)
                assert url is not None
                self.client.post(url.group(1))
            after = self.plan_page()
        self.assertNotIn('class="panel behind"', after)
        self.assertIn("Put this back", after)
