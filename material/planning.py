"""The schedule: what the student studies, on which days, and for how long.

Two modes, and the exam date is the only difference between them. With one, the
plan is anchored - sessions land on real study days, none of them on or after
the exam, and the countdown is shown. Without one the plan is open: the topics
in the order the student put them in, a rolling next session, and no countdown,
because counting down to a date the student does not have is pressure they did
not ask for. Open mode is what makes the app worth opening in a week with no
exam in sight.

Generation is arithmetic on the student's own topics, not a model call. Their
order is the one they confirmed, their weight says how much of a topic there
is, and the two numbers the student gave - days and hours - are the budget. A
model asked to "design a study plan" would return a plan the student cannot
check, and this one has to be checkable against a total they set themselves.

Two rules earn their place here rather than in the ticket's prose:

- **One topic is one session.** Never split across days, never two topics in one
  sitting. A half-finished topic is where abandonment starts, so the shape is in
  the schema and cannot be got wrong by a later edit.
- **Fewer sessions, not more.** The session count is the lesser of the days the
  student can study and the number their budget honestly carries, and each one
  is long enough to be worth opening. A student with an hour a week is offered
  two good sessions rather than five that would each be twelve minutes of
  deciding whether to bother. A plan the student believes is achievable is the
  whole product.

Availability the scheduler cannot work with is raised as
``material.topics.Rejected``, the same refusal the student's edits to the topic
path raise, so a page that shows either says the same kind of thing.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db import transaction
from django.utils import formats, timezone

from material.models import Course, Plan, Session, Topic
from material.topics import Rejected, require_confirmation
from material.users import current_user_id, owned

logger = logging.getLogger(__name__)

#: The shortest session worth offering. Below this a session is not a sitting
#: down to study but a decision about whether to start, and a week of those is
#: a week the student skips.
MIN_SESSION_MINUTES = 30

#: The longest session worth offering. A topic cannot be split across days, so
#: without a ceiling a small course and a large budget produce a single sitting
#: of the whole week, which is the thing the student will not do. Time a topic
#: cannot hold stays unspent rather than being promised as one session.
MAX_SESSION_MINUTES = 180

#: Sessions are offered in multiples of this. Five minutes is the smallest
#: difference a student can tell they are being treated differently, and it
#: keeps a week of sessions adding up to the budget the student stated.
ROUNDING = 5

MAX_DAYS_PER_WEEK = 7

#: Nobody studies more than a day and a night of a week, and the column that
#: holds the answer cannot take a bigger number than this either.
MAX_HOURS_PER_WEEK = 24

#: The days a week is measured in. Five days of study is the working week, and
#: Saturday and Sunday are only offered to a student who asks for them.
WORK_WEEK = 5


@dataclass(frozen=True)
class Availability:
    """What the student says they have, which is what the plan is built inside."""

    days_per_week: int
    hours_per_week: int
    exam_date: date | None = None

    @property
    def minutes_per_week(self) -> int:
        return self.hours_per_week * 60

    @property
    def sessions_per_week(self) -> int:
        """Sessions a week can honestly carry.

        The lesser of the days the student studies and the number the budget
        stretches to at the shortest session worth offering. The budget side is
        never zero, because a student cannot study less than an hour a week and
        is refused if they try.
        """
        return min(self.days_per_week, self.minutes_per_week // MIN_SESSION_MINUTES)


@dataclass(frozen=True)
class Week:
    """One week of the plan, as the student reads it."""

    number: int
    label: str
    sessions: tuple[Session, ...]

    @property
    def minutes(self) -> int:
        return sum(session.minutes for session in self.sessions)


@dataclass(frozen=True)
class Overview:
    """Everything the plan page shows, read off the plan in one pass.

    One query rather than one per question: the weeks, the session the student
    starts now and the topics that missed out are all the same rows, and a page
    that asks three times is a page that can disagree with itself.
    """

    weeks: tuple[Week, ...]
    current: Session | None
    unplaced: tuple[Topic, ...]
    days_left: int | None
    budget: int
    study_days: str

    @property
    def planned(self) -> int:
        """Minutes the first week of the plan actually uses."""
        return self.weeks[0].minutes if self.weeks else 0

    @property
    def passed(self) -> bool:
        """Whether every date on this plan is behind the student."""
        return self.current is None

    @property
    def empty(self) -> bool:
        """Whether the plan never managed to hold a session at all.

        Different from ``passed``: a plan with no sessions has not run out of
        days, it was built with none in it, which is what an exam tomorrow
        produces. Saying "every date has passed" about a plan that never had a
        date is how a page starts lying to a student with one day left.
        """
        return not any(week.sessions for week in self.weeks)

    @property
    def unspent(self) -> int:
        """Minutes of the student's own week the plan could not put anywhere.

        Non-zero when every session is already at the ceiling - one topic that
        cannot be split, and a budget too large for it. The student said they
        had that time, so saying what happened to it beats a total quietly
        falling short of the number they typed.
        """
        return max(0, self.budget - self.planned)


def plan_for(course: Course) -> Plan | None:
    """The course's plan, if the student has ever built one."""
    return owned(Plan.objects.filter(course=course)).first()


def today() -> date:
    """The day the student is living in, as the server sees it.

    Everything date-shaped asks here rather than reaching for the clock itself,
    so that "today" is one answer in one place and a week built on it is one
    week rather than three depending on which function asked.
    """
    return timezone.localdate()


def read_availability(form: Mapping[str, Any]) -> Availability:
    """What the student typed, or a refusal they can act on.

    Every number here is a claim about their own week, so the bounds are the
    student's real ones: nobody studies zero days a week, and nobody can read
    an hour a week. A date that cannot be read is refused rather than guessed,
    because guessing produces a countdown to the wrong day.
    """
    days = _number(form.get("days_per_week"), "days a week")
    hours = _number(form.get("hours_per_week"), "hours a week")
    if not 1 <= days <= MAX_DAYS_PER_WEEK:
        raise Rejected(
            f"Study at least one day a week, and no more than {MAX_DAYS_PER_WEEK}."
        )
    if not 1 <= hours <= MAX_HOURS_PER_WEEK:
        raise Rejected(
            f"Say between one and {MAX_HOURS_PER_WEEK} hours a week. A week has "
            "168 hours in it, and there is a point past which the answer is not "
            "about studying."
        )
    return Availability(days, hours, _exam_date(form.get("exam_date")))


def study_weekdays(days_per_week: int) -> list[int]:
    """Which days of the week the student studies.

    Spread across the working week rather than taken from the front of it,
    because a student who can study three days wants Monday, Wednesday and
    Friday and not Monday to Wednesday. Five days is the whole working week,
    and Saturday and Sunday only turn up for a student who asked for six or
    seven.
    """
    if days_per_week > WORK_WEEK:
        return list(range(days_per_week))
    if days_per_week == 1:
        return [0]
    last = WORK_WEEK - 1
    return sorted(
        {round(offset * last / (days_per_week - 1)) for offset in range(days_per_week)}
    )


def generate(course: Course, availability: Availability) -> Plan:
    """Rebuild the course's week of sessions from its confirmed topics.

    Replaces what is there rather than adjusting it: a schedule is a function of
    the topics, the order, and the time the student said they have, and changing
    any of those changes every minute and day it produced. The topic path is
    gated on confirmation, so nothing here can build a plan on topics the
    student has not checked.
    """
    topics = require_confirmation(course)
    plan, _ = Plan.objects.get_or_create(
        user_id=current_user_id(), course=course
    )
    plan.exam_date = availability.exam_date
    plan.days_per_week = availability.days_per_week
    plan.hours_per_week = availability.hours_per_week
    plan.save()
    weeks_of_days = _study_weeks(availability, today(), len(topics))
    with transaction.atomic():
        plan.sessions.all().delete()
        _write(plan, topics, availability, weeks_of_days)
    logger.info(
        "%s: %s plan of %d sessions on %d days a week, %d hours a week",
        course.title,
        plan.mode,
        plan.sessions.count(),
        availability.days_per_week,
        availability.hours_per_week,
    )
    return plan


def overview(plan: Plan, topics: Sequence[Topic]) -> Overview:
    """Everything the plan page shows, read off the plan's sessions once.

    The weeks, the session the student starts now, and the topics that did not
    get one are all the same rows, so they are all read together: three queries
    answering three questions about the same plan is a page that can disagree
    with itself.

    A topic with no session only happens when the exam arrives first. They are
    listed rather than dropped, because a topic silently missing from the plan
    is a topic the student has been told to study and cannot find.
    """
    sessions = list(owned(plan.sessions.all()))
    current = next(
        (s for s in sessions if s.scheduled_on >= today()),
        None,
    )
    scheduled = {session.topic_id for session in sessions}
    return Overview(
        weeks=_weeks(sessions, this_week=today()),
        current=current,
        unplaced=tuple(topic for topic in topics if topic.pk not in scheduled),
        days_left=days_until_exam(plan),
        budget=plan.hours_per_week * 60,
        study_days=_named_days(plan.days_per_week),
    )


def days_until_exam(plan: Plan) -> int | None:
    """Days left before the exam, or nothing at all when there is no exam."""
    if plan.exam_date is None:
        return None
    return (plan.exam_date - today()).days


def _weeks(sessions: Sequence[Session], *, this_week: date | None) -> tuple[Week, ...]:
    """The sessions grouped into the calendar weeks they actually fall in.

    Calendar weeks, not every Nth session: a plan started on a Thursday would
    otherwise put Thursday and Monday in one panel and call it a week, which is
    not what a student means by the week they are in.

    The week the student is living in is named "this week" and the rest are
    numbered from the start of the plan, so "week 3" keeps meaning the same
    three days however long after the plan was built they open it. Only the
    "this week" label moves, and it moves because the student has.
    """
    grouped: dict[tuple[int, int], list[Session]] = {}
    for session in sessions:
        grouped.setdefault(session.scheduled_on.isocalendar()[:2], []).append(session)
    this = _week_of(this_week) if this_week is not None else None
    return tuple(
        Week(
            number=number,
            label="This week" if key == this else f"Week {number}",
            sessions=tuple(week_sessions),
        )
        for number, (key, week_sessions) in enumerate(sorted(grouped.items()), start=1)
    )


def _week_of(day: date) -> tuple[int, int]:
    """The calendar week a day falls in, as the pair weeks are keyed by."""
    return day.isocalendar()[:2]


def _named_days(days_per_week: int) -> str:
    """The days the plan was put on, in words.

    The student chose a number of days rather than the days themselves, so the
    page says which ones it picked. A student whose free days are Saturday and
    Sunday can see straight away that the plan has assumed weekdays, which is
    the difference between a plan they can fix and one they abandon.
    """
    # A Monday, so the offsets line up with ``date.weekday()``, named through
    # Django's own formatter so they come out in the page's language.
    monday = date(2024, 1, 1)
    names = [
        formats.date_format(monday + timedelta(days=offset), "l")
        for offset in range(7)
        if offset in study_weekdays(days_per_week)
    ]
    if len(names) == 1:
        return f"{names[0]}s"
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _write(
    plan: Plan,
    topics: Sequence[Topic],
    availability: Availability,
    weeks_of_days: Sequence[Sequence[date]],
) -> None:
    """One session per topic that fits, in the student's order, weighted.

    Each calendar week of the plan takes the topics it has room for and is given
    the student's weekly budget to divide between them. Topics that did not fit
    before the exam are the ones at the end, and the page names them rather
    than leaving the student to notice they are not on it.
    """
    taken = 0
    for week_of_days in weeks_of_days:
        chunk = topics[taken : taken + len(week_of_days)]
        if not chunk:
            break
        # The last week of a plan is short of days rather than of topics.
        days = list(week_of_days[: len(chunk)])
        minutes = _split_week(
            availability.minutes_per_week, [topic.weight for topic in chunk]
        )
        for offset, (topic, day, length) in enumerate(
            zip(chunk, days, minutes, strict=True)
        ):
            Session.objects.create(
                user_id=current_user_id(),
                plan=plan,
                topic=topic,
                position=taken + offset + 1,
                minutes=length,
                scheduled_on=day,
            )
        taken += len(chunk)


def _split_week(budget: int, weights: Sequence[int]) -> list[int]:
    """Share one week's minutes between its sessions by topic weight.

    A topic worth three times the work of another gets three times the minutes,
    because that is the claim the topic path already made about itself. The
    share is rounded down first so that no session over-promises, then the week
    is settled: the minutes rounding left over go to whoever is furthest below
    what their weight says, and if the floor for a short session has pushed the
    week over the budget they come back off the sessions that are furthest
    above theirs. That order matters - doing it the other way round leaves a
    week the student has no time for.

    Two topics the student gave the same weight can still differ by one step.
    Their exact shares are fractional and the leftover is a whole number of
    steps, so somebody has to get it; the tie is broken towards the topic
    earlier in the course, which is a rule the student can see rather than one
    they have to discover.
    """
    total = sum(weights) or 1
    exact = [budget * weight / total for weight in weights]
    minutes = [
        min(
            MAX_SESSION_MINUTES,
            max(MIN_SESSION_MINUTES, int(value) // ROUNDING * ROUNDING),
        )
        for value in exact
    ]
    while sum(minutes) > budget:
        overfilled = _most_overfilled(minutes, exact)
        if overfilled is None:
            break
        minutes[overfilled] -= ROUNDING
    spare = budget - sum(minutes)
    for _ in range(spare // ROUNDING):
        index = _shortest_filled(minutes, exact)
        if minutes[index] >= MAX_SESSION_MINUTES:
            # This one cannot hold any more. The rest of the week can, so keep
            # going round rather than leaving the student's own time unspent
            # because the topic that was owed most is already at its ceiling.
            continue
        minutes[index] += ROUNDING
    return minutes


def _most_overfilled(minutes: Sequence[int], exact: Sequence[float]) -> int | None:
    """The session furthest above what its weight says, earliest in the course
    first, or nothing when every session is already at the floor.

    The floor for a short session is the one thing that can push a week over the
    budget, and the minutes have to come back off somewhere. Taking them off
    the furthest overfilled keeps the split in proportion, so trimming never
    hands a bigger topic less time than a smaller one.
    """
    above_floor = [
        index for index, length in enumerate(minutes) if length > MIN_SESSION_MINUTES
    ]
    if not above_floor:
        return None
    return max(above_floor, key=lambda index: (minutes[index] - exact[index], -index))


def _shortest_filled(minutes: Sequence[int], exact: Sequence[float]) -> int:
    """Whose claim on the week is least met, earliest in the course first.

    Choosing by what is still owed rather than by who is currently longest is
    what keeps the split proportional, and it is why a bigger topic can never
    end up with a shorter session than a smaller one.
    """
    return max(
        range(len(minutes)),
        key=lambda index: (exact[index] - minutes[index], -index),
    )


def _study_weeks(availability: Availability, start: date, limit: int) -> list[list[date]]:
    """The days sessions land on, filled one calendar week at a time.

    A list of weeks rather than a flat run of days, because a week is what the
    budget is for and what the student reads. At most ``sessions_per_week`` days
    per calendar week, which is what keeps "fewer sessions, not more" true of
    the weeks on the page rather than of an internal list: taking study days off
    one flat run would put a fortnight of Monday-to-Friday study into a single
    week whenever the plan was built on a Thursday.

    Exam mode stops at the exam: nothing is scheduled on the day itself or
    after it, because a session on exam day is a session that cannot happen. A
    week that comes back empty ends the walk, and the topics still without a
    day are reported rather than dropped.

    Open mode has no horizon to stop at and does not need one. Every week holds
    at least one study day, so the walk ends as soon as every topic has a day,
    however many topics there are and however far apart they land.
    """
    weekdays = study_weekdays(availability.days_per_week)
    per_week = availability.sessions_per_week
    until = availability.exam_date
    filled: list[list[date]] = []
    placed = 0
    day = start
    while placed < limit:
        if until is not None and day >= until:
            break
        week_end = day + timedelta(days=7 - day.weekday())
        week: list[date] = []
        while day < week_end and len(week) < per_week:
            if day.weekday() in weekdays and (until is None or day < until):
                week.append(day)
            day += timedelta(days=1)
        if not week:
            # This calendar week has no study day left in it - the student
            # studies Mondays and it is Tuesday - so carry on to the next one
            # rather than ending the plan. The exam check at the top of the
            # loop is what ends it, and that check still applies.
            day = max(day, week_end)
            continue
        filled.append(week)
        placed += len(week)
        day = max(day, week_end)
    return filled


def _number(raw: Any, what: str) -> int:
    """A whole number the student typed, or a refusal naming the field."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise Rejected(f"Say how many {what} as a whole number.") from None


def _exam_date(raw: Any) -> date | None:
    """The exam date, or nothing when the student has not got one yet.

    An open plan is a normal thing to have, so an empty date is not a mistake
    and is not an error. A date that is present but unreadable is a different
    thing: it is almost certainly a mistyped one, and counting down to the wrong
    day is worse than asking.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise Rejected("Give the exam date as a day, like 2026-11-14.") from None
