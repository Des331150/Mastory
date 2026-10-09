"""The schedule: what the student studies, on which days, and for how long.

Two modes, and the exam date is the only difference between them. With one,
the plan is anchored - sessions land on real study days, none of them on or
after the exam, and the countdown is shown. Without one the plan is open: the
topics in the order the student put them in, a rolling next session, and no
countdown, because counting down to a date the student does not have is
pressure they did not ask for. Open mode is what makes the app worth opening in
a week with no exam in sight.

Generation is arithmetic on the student's own topics, not a model call. Their
order is the one they confirmed, their weight says how much of a topic there
is, and the two numbers the student gave - days and hours - are the budget. A
model asked to "design a study plan" would return a plan the student cannot
check, and this one has to be checkable against a total they set themselves.

Two rules earn their place here rather than in the ticket's prose:

- **One topic is one session.** Never split across days, never two topics in
  one sitting. A half-finished topic is where abandonment starts, so the shape
  is in the schema and cannot be got wrong by a later edit.
- **Fewer sessions, not more.** The session count is the lesser of the days the
  student can study and the number their budget honestly carries, and each one
  is long enough to be worth opening. A student with two hours a week is
  offered two good sessions, not five that would each be fifteen minutes of
  deciding whether to bother. A plan the student believes is achievable is the
  whole product.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from material.models import Course, Plan, Session, Topic
from material.topics import require_confirmation
from material.users import current_user_id, owned

logger = logging.getLogger(__name__)

#: The shortest session worth offering. Below this a session is not a sitting
#: down to study but a decision about whether to start, and a week of those is
#: a week the student skips.
MIN_SESSION_MINUTES = 30

#: The longest session worth offering. A topic cannot be split across days, so
#: without a ceiling a small course and a large budget produce a single sitting
#: of the entire week, which is the thing the student will not do. Time a topic
#: cannot hold stays unspent rather than being promised as one session.
MAX_SESSION_MINUTES = 180

#: Sessions are offered in multiples of this. Five minutes is the smallest
#: difference a student can tell they are being treated differently, and it
#: keeps a week of sessions adding up to the budget the student stated.
ROUNDING = 5

MAX_DAYS_PER_WEEK = 7
DEFAULT_DAYS_PER_WEEK = 3
DEFAULT_HOURS_PER_WEEK = 6

#: Long enough to be bounded even when a student's exam is a year out, so a
#: mistyped date cannot walk the scheduler through every day until it gives up.
MAX_HORIZON_DAYS = 400


class Rejected(Exception):
    """Availability the scheduler cannot work with, in words the student can use."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
        stretches to at the shortest session worth offering. Capped at one: a
        student who can study an hour a week still gets a session, because a
        plan with nothing in it helps nobody.
        """
        return max(
            1, min(self.days_per_week, self.minutes_per_week // MIN_SESSION_MINUTES)
        )


@dataclass(frozen=True)
class Week:
    """One calendar week of the plan, as the student reads it."""

    number: int
    label: str
    sessions: tuple[Session, ...]

    @property
    def minutes(self) -> int:
        return sum(session.minutes for session in self.sessions)


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
    if hours < 1:
        raise Rejected(
            "Say at least one hour a week. Less than that is not a week of study."
        )
    return Availability(days, hours, _exam_date(form.get("exam_date")))


def study_weekdays(days_per_week: int) -> list[int]:
    """Which days of the week the student studies, spread across it from Monday.

    Evenly spread rather than the first ``days_per_week`` weekdays, because a
    student who can study three days wants them spread through the week and not
    clumped into Monday to Wednesday. Three days is Monday, Wednesday, Friday.
    """
    return sorted({offset * 7 // days_per_week for offset in range(days_per_week)})


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
    days = _study_dates(availability, today(), len(topics))
    with transaction.atomic():
        plan.sessions.all().delete()
        _write(plan, topics, availability, days)
    logger.info(
        "%s: %s plan of %d sessions on %d days a week, %d hours a week",
        course.title,
        plan.mode,
        plan.sessions.count(),
        availability.days_per_week,
        availability.hours_per_week,
    )
    return plan


def weeks(plan: Plan, *, this_week: int | None = None) -> list[Week]:
    """The plan as the weeks the student lives in, the next one named.

    ``this_week`` is the week the next session falls in, which is the one the
    student is actually in. The rest keep their number: a plan that renumbers
    itself every time a session passes would be a plan the student cannot talk
    about. ``week`` is stored from zero and counted from one here, because zero
    is an index and the student is not reading an index.
    """
    grouped: dict[int, list[Session]] = {}
    for session in owned(plan.sessions.all()):
        grouped.setdefault(session.week, []).append(session)
    return [
        Week(
            number=number,
            label="This week" if number == this_week else f"Week {number + 1}",
            sessions=tuple(sessions),
        )
        for number, sessions in sorted(grouped.items())
    ]


def next_session(plan: Plan) -> Session | None:
    """The session the student starts now: today's if there is one, else the next.

    None once every session on the plan has a date in the past. That is not a
    plan that ran out of topics, it is a plan whose dates have gone by, and the
    answer to it is a fresh week rather than a session from last month.
    """
    current = today()
    for session in owned(plan.sessions.all()):
        if session.scheduled_on >= current:
            found: Session = session
            return found
    return None


def unplaced(plan: Plan, topics: Sequence[Topic]) -> list[Topic]:
    """Topics with no session, which only happens when the exam arrives first.

    They are listed rather than dropped: a topic silently missing from the plan
    is a topic the student has been told to study and cannot find.
    """
    scheduled = {
        session.topic_id for session in owned(plan.sessions.all()).select_related("topic")
    }
    return [topic for topic in topics if topic.pk not in scheduled]


def days_until_exam(plan: Plan) -> int | None:
    """Days left before the exam, or nothing at all when there is no exam."""
    if plan.exam_date is None:
        return None
    return (plan.exam_date - today()).days


def _write(
    plan: Plan, topics: Sequence[Topic], availability: Availability, days: Sequence[date]
) -> None:
    """One session per topic that fits, in the student's order, weighted.

    A week is filled from the topics the student confirmed and then the next
    week starts, so the topics that do not fit before the exam are simply the
    ones at the end. The page names them rather than leaving the student to
    notice they are not on it.
    """
    per_week = availability.sessions_per_week
    for offset in range(0, len(topics), per_week):
        placed = days[offset : offset + per_week]
        chunk = topics[offset : offset + len(placed)]
        if not chunk:
            break
        minutes = _split_week(
            availability.minutes_per_week, [topic.weight for topic in chunk]
        )
        for index, (topic, day, length) in enumerate(zip(chunk, placed, minutes, strict=True)):
            study_day = offset + index
            Session.objects.create(
                user_id=current_user_id(),
                plan=plan,
                topic=topic,
                position=study_day + 1,
                study_day=study_day,
                week=study_day // per_week,
                minutes=length,
                scheduled_on=day,
            )


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
            break
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


def _study_dates(
    availability: Availability, start: date, limit: int
) -> list[date]:
    """The days sessions land on, from today onwards.

    Exam mode stops at the exam: nothing is scheduled on the day itself or after
    it, because a session on exam day is a session that cannot happen. If that
    leaves topics without a day they are reported rather than quietly dropped.
    """
    weekdays = study_weekdays(availability.days_per_week)
    horizon = min(
        MAX_HORIZON_DAYS,
        (availability.exam_date - start).days
        if availability.exam_date is not None
        else MAX_HORIZON_DAYS,
    )
    dates: list[date] = []
    day = start
    while len(dates) < limit and (day - start).days < horizon:
        if day.weekday() in weekdays:
            dates.append(day)
        day += timedelta(days=1)
    return dates


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