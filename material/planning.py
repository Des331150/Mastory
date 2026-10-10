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

A third rule is about what happens when the student falls behind, and it is the
one that decides whether this product is worth coming back to:

- **Missed sessions shift later. They never compress.** A day that went by
  unmarked does not hand its day back to the work still waiting: the remaining
  topics move to the next study days, and the day the student said they would
  finish moves with them. Fitting the same work into the days that are left is
  how a planner produces a plan the student already knows is impossible, and an
  impossible plan is how they stop opening the app.
- **Finished work gives nothing back either.** A rebuild re-plans what is ahead,
  not what is behind, so a session the student completed keeps the day it was on
  and the work still waiting starts from today. See ``generate``.
- **When it will not fit, say the day.** The finish date the student is shown is
  the one their remaining work actually reaches at the pace they said they can
  manage, with the exam horizon lifted off it. An answer that stopped at the
  exam would only restate the problem. See ``behind``.
- **Dropping a topic is the student's call, and it says what it costs.** What
  cutting buys - minutes a week, a finish date a week earlier - is on the page
  before the button, never after it. See ``cut``.

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
class Cut:
    """What taking one topic off the week would cost the student, and buy them.

    Both halves are needed and neither is enough. ``minutes`` is what the
    topic takes out of the week the student budgeted for themselves, which is
    the currency they already think in. ``finishes_on`` is the day they would
    stop, which is the answer to the question that made them open the page, and
    it is the same for every topic on offer: each one frees exactly one session,
    and the remaining sessions keep their order.

    ``finishes_on`` is nothing when only one session is left to do, because
    cutting the last one leaves nothing to finish - an edge case rather than a
    thing to dress up as a date.
    """

    topic: Topic
    minutes: int
    finishes_on: date | None


@dataclass(frozen=True)
class Behind:
    """Where the student really is, and what they could do about it.

    Only ever built when the work left does not finish before the exam. In open
    mode there is no deadline to be late against, so there is nothing to say,
    and ``None`` is the honest answer rather than a panel with a made-up clock
    in it.

    ``sessions_left`` is here because the student should be able to check the
    date against their own arithmetic: seven sessions at three a week is the
    thirtieth, and being told that is checkable in a way that being told a date
    is not.

    ``kept_out`` are the topics the student has already cut, so that the panel
    which offers to drop something is also the panel that can put one back. A
    cut the student cannot take back is a cut made in a hurry, which is exactly
    when they will make the wrong one.
    """

    projected_on: date
    late_by: int
    sessions_left: int
    cuts: tuple[Cut, ...]
    kept_out: tuple[Topic, ...]


@dataclass(frozen=True)
class Overview:
    """Everything the plan page shows, read off the plan in one pass.

    One query rather than one per question: the weeks, the session the student
    starts now, the topics that missed out and the sessions they have already
    recorded are all the same rows, and a page that asks three times is a page
    that can disagree with itself. The flat list is on here for that reason -
    ``material.progress`` counts it without a second query.
    """

    sessions: tuple[Session, ...]
    weeks: tuple[Week, ...]
    current: Session | None
    unplaced: tuple[Topic, ...]
    days_left: int | None
    budget: int
    study_days: str
    behind: Behind | None

    @property
    def planned(self) -> int:
        """Minutes the first week of the plan actually uses."""
        return self.weeks[0].minutes if self.weeks else 0

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


def availability_of(plan: Plan) -> Availability:
    """The week a plan was built from, as the numbers the student gave.

    Read back off the plan rather than kept in the request, so that anything
    which rebuilds the plan later - a cut today, a change of hours tomorrow -
    rebuilds it from what was actually agreed rather than from what the last
    form happened to post.
    """
    return Availability(plan.days_per_week, plan.hours_per_week, plan.exam_date)


def generate(course: Course, availability: Availability) -> Plan:
    """Rebuild the course's week of sessions from its confirmed topics.

Replaces what is there rather than adjusting it: a schedule is a function of
    the topics, the order, and the time the student said they have, and changing
    any of those changes every minute and day it produced. The topic path is
    gated on confirmation, so nothing here can build a plan on topics the
    student has not checked.

Two rules make the rebuild safe to run on a plan with a history on it, and
both of them are what "shift, never compress" means in practice:

- **The work the student has finished keeps its day and gives nothing back.**
  It is not re-planned into the future and it does not free a slot. A rebuild
  after a fortnight of doing nothing is how the remaining topics land further
  down the calendar, which is the honest reading of two lost weeks.
- **The work still waiting starts from today.** Missed days are not refilled
  and are not compressed: the next topic goes on the next study day, and the
  one after that on the one after that.

Nothing the student has recorded is touched. That lives on the topic, not
on the session, precisely so that a rebuild cannot lose it - see
``material.models.Topic``.
    """
    topics = require_confirmation(course)
    plan, _ = Plan.objects.get_or_create(
        user_id=current_user_id(), course=course
    )
    plan.exam_date = availability.exam_date
    plan.days_per_week = availability.days_per_week
    plan.hours_per_week = availability.hours_per_week
    plan.save()
    held = _held(plan)
    planned = [topic for topic in topics if not topic.cut]
    done = [topic for topic in planned if topic.recorded_on is not None]
    ahead = [topic for topic in planned if topic.recorded_on is None]
    weeks_of_days = _study_weeks(availability, today(), len(ahead))
    with transaction.atomic():
        plan.sessions.all().delete()
        _write(plan, ahead, availability, weeks_of_days, done=done, held=held)
    logger.info(
        "%s: %s plan of %d sessions on %d days a week, %d hours a week",
        course.title,
        plan.mode,
        plan.sessions.count(),
        availability.days_per_week,
        availability.hours_per_week,
    )
    return plan


def _held(plan: Plan) -> dict[int, tuple[date, int]]:
    """Where the plan's recorded sessions already sit, before it is rebuilt.

    Read before the delete and used to put them back where they were. A
    completed session whose date came from the rebuild would be booked on a day
    that has not happened yet, which is a record of the plan rather than of the
    student.
    """
    return {
        session.topic_id: (session.scheduled_on, session.minutes)
        for session in owned(plan.sessions.all())
        if session.recorded_on is not None
    }


def cut(course: Course, topic: Topic) -> Plan:
    """Take one topic off the student's week, or put it back on it.

A cut is a decision about the week, not a deletion from the course: the topic
    keeps its place on the path and everything the student pointed at it, and
    the plan is rebuilt around its absence. That rebuild is what makes a cut
    work - the days the topic was on are given back to the remaining work,
    which is the only way cutting anything ever finishes a plan earlier.

It is a toggle, like every other mark on this page. A student who cuts the
    wrong topic should not have to find an undo to put it back.

The day is written to the topic rather than read from the request, so that the
    decision survives a rebuild the way a completion does.
    """
    topic.cut_on = None if topic.cut else today()
    topic.save(update_fields=["cut_on"])
    plan = plan_for(course)
    if plan is None:
        raise Rejected("There is no week to change yet.")
    logger.info("%s: %s %s", course.title, "kept" if topic.cut else "cut", topic.title)
    return generate(course, availability_of(plan))


def _ahead(topics: Sequence[Topic]) -> list[Topic]:
    """The topics still to be studied: not finished, not skipped, not cut."""
    return [topic for topic in topics if topic.recorded_on is None and not topic.cut]


def _pace_days(plan: Plan, count: int) -> list[date]:
    """The days ``count`` sessions take at the pace the student said they have.

    Deliberately with no exam horizon on it. This is the question "if I keep
    going at the rate I said I could, what day do I stop on?" and an answer that
    stopped at the exam would only restate the problem back at the student.

    The days come from the same walk the plan is built from, so the projected
    finish is the last day of a plan shaped like this one and not a separate
    guess that can drift from it. The exam date is deliberately not passed on:
    this walk is the one place in the module with no horizon.
    """
    weeks = _study_weeks(
        Availability(plan.days_per_week, plan.hours_per_week), today(), count
    )
    # The walk fills a whole week before it checks how much it has already
    # placed, so the last week comes back with a day or two more than were
    # asked for. Those days are not part of this projection.
    return [day for week in weeks for day in week][:count]


def behind(plan: Plan, topics: Sequence[Topic]) -> Behind | None:
    """Whether the work left finishes before the exam, and what to do if not.

    Nothing in open mode: with no deadline there is nothing to be late against,
    and inventing one would be pressure the student did not ask for.

    The projection lifts the exam horizon off and counts only the work the
    student has not recorded, so a fortnight spent doing nothing moves the date
    by a fortnight's worth of sessions rather than by nothing at all. The topics
    on offer to cut are the same ones, costed in the minutes they take out of
    the week and the day the student would stop.
    """
    ahead = _ahead(topics)
    if plan.exam_date is None or not ahead:
        return None
    days = _pace_days(plan, len(ahead))
    if not days or days[-1] <= plan.exam_date:
        return None
    minutes = _split_week(plan.hours_per_week * 60, [topic.weight for topic in ahead])
    # Cutting any one topic frees exactly one session and the rest keep their
    # order, so every option lands on the same day. That is not a shortcut, it
    # is the arithmetic: saying it eight times with eight dates would be eight
    # claims to check and one answer.
    after = days[-2] if len(days) > 1 else None
    return Behind(
        projected_on=days[-1],
        late_by=(days[-1] - plan.exam_date).days,
        sessions_left=len(ahead),
        cuts=tuple(
            Cut(topic=topic, minutes=length, finishes_on=after)
            for topic, length in zip(ahead, minutes, strict=True)
        ),
        kept_out=tuple(topic for topic in topics if topic.cut),
    )


def overview(plan: Plan, topics: Sequence[Topic]) -> Overview:
    """Everything the plan page shows, read off the plan's sessions once.

    The weeks, the session the student starts now, and the topics that did not
    get one are all the same rows, so they are all read together: three queries
    answering three questions about the same plan is a page that can disagree
    with itself.

    A topic with no session only happens when the exam arrives first, or when
    the student has cut it. Cut topics are left out of that list deliberately:
    they are not topics that missed out, they are ones the student put there on
    purpose, and listing them next to the ones they cannot reach would read as
    a problem rather than a decision.
    """
    sessions = list(owned(plan.sessions.all()))
    current = next(
        (s for s in sessions if s.scheduled_on >= today()),
        None,
    )
    scheduled = {session.topic_id for session in sessions}
    return Overview(
        sessions=tuple(sessions),
        weeks=_weeks(sessions, this_week=today()),
        current=current,
        unplaced=tuple(
            topic for topic in topics if topic.pk not in scheduled and not topic.cut
        ),
        days_left=days_until_exam(plan),
        budget=plan.hours_per_week * 60,
        study_days=_named_days(plan.days_per_week),
        behind=behind(plan, topics),
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
    *,
    done: Sequence[Topic] = (),
    held: Mapping[int, tuple[date, int]] | None = None,
) -> None:
    """One session per topic that fits, in the student's order, weighted.

    Each calendar week of the plan takes the topics it has room for and is given
    the student's weekly budget to divide between them. Topics that did not fit
    before the exam are the ones at the end, and the page names them rather
    than leaving the student to notice they are not on it.

    ``done`` is the work the student has already recorded and ``held`` says
    where each of those sessions sat before the rebuild. They keep that day and
    those minutes, because the week a finished topic took up is spent: the
    remaining topics divide the budget that is left, not a budget shared with
    work already out of the way.

    Everything is written in one pass ordered by the day it falls on, so a
    session that shifted later sits after the ones that did not move and the
    plan reads down the calendar rather than down two lists stitched together.
    """
    kept = held or {}
    rows: list[tuple[Topic, date, int]] = []
    for topic in done:
        # A recorded topic always has a session to have come from. Falling back
        # to the shortest session worth offering rather than to zero is what
        # keeps a session off the page claiming it is nothing long.
        day, length = kept.get(topic.pk, (topic.recorded_on, MIN_SESSION_MINUTES))
        if day is not None:
            rows.append((topic, day, length))
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
        rows.extend(zip(chunk, days, minutes, strict=True))
        taken += len(chunk)
    for position, (topic, day, length) in enumerate(
        sorted(rows, key=lambda row: row[1]), start=1
    ):
        Session.objects.create(
            user_id=current_user_id(),
            plan=plan,
            topic=topic,
            position=position,
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
