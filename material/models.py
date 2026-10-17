"""The extraction layer: Course -> File -> Slide -> Span, the pointer map
Course -> Topic -> TopicSlide, the plan Course -> Plan -> Session, the record
the student keeps on a topic: ``Topic.completed_on``, the per-topic ``Attempt``
with the paragraphs it sent them back to, and the ``SectionOpen`` saying they
went, and the exam that spans the whole course: Course -> MockExam ->
MockQuestion, and ``Spend``: one row per piece of work the student caused, with
what it cost.

Every table carries a ``user_id`` so that adding real authentication in v1 is a
filter rather than a refactor.
"""

from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import models

from material.users import owned

#: What a student is told about a quiz with too few verified questions to mean
#: much. Kept beside the table because it is the flag's meaning, and the flag is
#: read in three places that must not each phrase it differently.
LOW_CONFIDENCE = (
    "Only a little of this topic could be checked against your own slides, so "
    "this score says less than a longer one would. Read the paragraphs it "
    "cites."
)

#: The share of a quiz a sitting has to reach to count as having got it, and
#: below which it counts as a failed attempt. Kept beside ``Attempt`` for the
#: same reason the flag's wording is: it is the definition of a word the page
#: uses, and two places answering it differently is how a student gets refused a
#: retry on a score one of them thought was a pass. Half right is the line
#: because a quiz of four is a topic they half know, and sitting it again is the
#: sensible thing for a student to do with one.
PASS_MARK = 0.5


def is_failure(score: float) -> bool:
    """Whether a score is short of ``PASS_MARK``.

    One function, so that "failed" has a single answer in the codebase. It is read
    on the row that recorded a sitting, on the result the student is shown before
    any row is written, and on the table of where each topic stands, and two of
    those answering differently is how a student gets told one thing about a quiz
    and another about its own score.
    """
    return score < PASS_MARK


def price(tokens: int | None) -> float | None:
    """What so many tokens cost at the rate this deployment is configured with.

    ``None`` for both of the things that are not a cost of nothing: tokens a
    provider never reported, and a model nobody has said what a token costs.
    Zero for either would print ``0.00`` on a page whose whole job is to say what
    serving a student cost, and a student reading that would believe the course
    was free.

    One rate rather than one for what went in and one for what came back, because
    the rate is one number whoever runs the server sets and a half-rate nobody has
    told us would be a half-rate we invented.
    """
    if tokens is None or not settings.MODEL_USD_PER_MILLION_TOKENS:
        return None
    return tokens * settings.MODEL_USD_PER_MILLION_TOKENS / 1_000_000


class Course(models.Model):
    """Material for one course the student is studying."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="courses"
    )
    title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.title

    @property
    def slides(self) -> list["Slide"]:
        """Every slide of every readable file, in the order the course teaches.

        Files are read in the order they were uploaded and slides in page order,
        which is what makes a course made of twelve weekly decks still read as
        one course rather than twelve. Scoped by the owning user like every
        other query, so the property cannot become a way to see another
        student's material.
        """
        readable = owned(self.files.filter(status=SourceFile.Status.READY)).order_by("id")
        slides: list[Slide] = []
        for source_file in readable:
            slides.extend(owned(source_file.slides.all()).order_by("number"))
        return slides

    @property
    def slide_count(self) -> int:
        return len(self.slides)


class SourceFile(models.Model):
    """One uploaded PDF, its extracted Markdown, and the retained original."""

    class Status(models.TextChoices):
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="source_files"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="files"
    )
    original = models.FileField(upload_to="originals/%Y/%m/")
    original_name = models.CharField(max_length=255)
    content_sha256 = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=8, choices=Status, default=Status.READY)
    failure_reason = models.TextField(blank=True)
    failure_advice = models.TextField(blank=True)
    page_count = models.PositiveIntegerField(default=0)
    stages = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return self.original_name

    @property
    def slug(self) -> str:
        """What keeps two sections of one course apart in the reading surface.

        A course can hold several files, and two of them can hold identical
        bytes, so this cannot be derived from the content: the row's own id is
        what makes every anchor in a page unique.
        """
        return str(self.pk)

    @property
    def image_dir(self) -> Path:
        """Where this file's extracted images live, one directory per file."""
        return Path(settings.MEDIA_ROOT) / "slides" / str(self.pk)


class Slide(models.Model):
    """One page of a PDF, as Markdown, addressable by a stable anchor."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="slides"
    )
    source_file = models.ForeignKey(
        SourceFile, on_delete=models.CASCADE, related_name="slides"
    )
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    markdown = models.TextField()
    plain_text = models.TextField(blank=True)
    images = models.JSONField(default=list)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_file", "number"], name="unique_slide_number_per_file"
            )
        ]

    def __str__(self) -> str:
        return f"{self.source_file.original_name} p{self.number}"

    def image_path(self, name: str) -> Path:
        """Where one of this slide's extracted images lives."""
        return self.source_file.image_dir / name

    @property
    def anchor(self) -> str:
        """A link target that stays the same across requests and searches."""
        return f"{self.source_file.slug}-p{self.number}"

    def span_anchor(self, ordinal: int) -> str:
        """A link target for one paragraph of this slide.

        A citation points at a span rather than at the slide it sits on: a
        wrong answer has to send the student to the paragraph the question came
        from, not to the top of a page they then have to search. The paragraph's
        place on the slide is its ``Span.ordinal``, so the target is derived
        here, in one place, and every reader of the slide - the reading page,
        the quiz, a later exam - builds the same string from it.
        """
        return f"{self.anchor}-s{ordinal}"


class Span(models.Model):
    """A contiguous run of text within a slide.

    Spans are the unit questions will later be generated from and cited to, so
    they are stored per slide with their offsets into that slide's text.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="spans"
    )
    slide = models.ForeignKey(
        Slide, on_delete=models.CASCADE, related_name="spans"
    )
    ordinal = models.PositiveIntegerField()
    text = models.TextField()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["slide", "ordinal"], name="unique_span_ordinal_per_slide"
            )
        ]

    def __str__(self) -> str:
        return f"{self.slide.anchor}#{self.ordinal}"

    @property
    def anchor(self) -> str:
        """Where a question citing this span sends the student."""
        return self.slide.span_anchor(self.ordinal)

    @property
    def citation(self) -> str:
        """How the span is named on the page: which slide, which paragraph.

        The student is shown this before they answer, because the promise this
        product makes - every question came from your own material - is only
        worth anything if the student can see which part is being claimed.
        """
        return (
            f"{self.slide.source_file.original_name}, page {self.slide.number}, "
            f"paragraph {self.ordinal + 1}"
        )


class TopicPath(models.Model):
    """Whether the student has confirmed the course's topic path.

    A wrong topic path costs nothing while the student can still fix it and is
    fatal once a schedule has been built on it, so confirmation is a gate on
    schedule generation rather than a preference. It is its own row rather than
    a field on ``Course`` because the confirmation carries a moment worth
    keeping and because re-inferring later must be able to clear it.
    """

    class State(models.TextChoices):
        DRAFT = "draft", "Awaiting confirmation"
        CONFIRMED = "confirmed", "Confirmed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="topic_paths"
    )
    course = models.OneToOneField(
        Course, on_delete=models.CASCADE, related_name="topic_path"
    )
    state = models.CharField(
        max_length=10, choices=State, default=State.DRAFT
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.course.title}: {self.get_state_display()}"


class Topic(models.Model):
    """One topic of the course, and the student's position on it.

    A topic is the unit the schedule, the quiz and mastery are all built on, so
    it has to be able to point at several slides that are not next to each
    other. That pointer map is the core asset: ``weight`` is derived from it and
    a question generated later is cited back through it.

    The student's own record of a topic - done, or deliberately skipped, and on
    what day - lives here rather than on the session that sat in front of them.
    That is deliberate: a session is the plan's row, and the plan is rebuilt
    whenever the student changes their hours or their exam date, so anything
    stored on a session is destroyed by a rebuild. A topic is the thing the
    student finished, the plan moves it around, and the record stays put.

    ``cut_on`` is the third day-shaped decision stored here rather than on the
    session, and for the same reason: it is the student saying they are not
    spending a week on this topic, which the plan rebuilds around rather than
    loses.
    """

    DONE = "done"
    SKIPPED = "skipped"
    PLANNED = "planned"
    CUT = "cut"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="topics"
    )
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="topics")
    title = models.CharField(max_length=255)
    position = models.PositiveIntegerField(default=0)
    weight = models.PositiveIntegerField(default=1)
    flagged = models.BooleanField(default=False)
    flag_note = models.TextField(blank=True)
    completed_on = models.DateField(null=True, blank=True)
    skipped_on = models.DateField(null=True, blank=True)
    cut_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "position"], name="unique_topic_position_per_course"
            )
        ]

    @property
    def state(self) -> str:
        """Where this topic stands in the student's record.

        Four answers and no others. There is deliberately no state for "the
        student missed this": a day that went by unmarked is the ordinary case
        for a student with a life, and naming it as a failure is the streak
        mechanic this product turned down.

        ``CUT`` is a decision about the plan rather than about the work: the
        student said they are not spending a week on this topic, which is
        different from having decided not to do it and different again from
        having run out of days before the exam.
        """
        if self.completed_on is not None:
            return self.DONE
        if self.skipped_on is not None:
            return self.SKIPPED
        if self.cut_on is not None:
            return self.CUT
        return self.PLANNED

    @property
    def cut(self) -> bool:
        """Whether the student has taken this topic off their week.

        Read rather than stored as its own column because the day they cut it is
        worth keeping the way the day they skipped it is: both are a decision
        made on a date, and neither is something the plan can afford to lose on
        a rebuild.
        """
        return self.cut_on is not None

    @property
    def recorded_on(self) -> date | None:
        """The day this topic entered the log, whichever way it entered it.

        A cut is not an entry in the log - nothing was done - so it is not one
        of the two days this answers with.
        """
        return self.completed_on or self.skipped_on

    def __str__(self) -> str:
        return self.title

    @property
    def slides(self) -> list[Slide]:
        """The slides this topic covers, in course order.

        Non-contiguous by nature: a topic that comes back to an idea covers the
        slides either side of the digression too.
        """
        return [pointer.slide for pointer in self.pointers.select_related("slide")]

    @property
    def span_count(self) -> int:
        """How much text this topic covers, which is most of its weight."""
        return sum(slide.spans.count() for slide in self.slides)


class Plan(models.Model):
    """What the student has to work with: a week of sessions, and a deadline.

    Exam mode and open mode are one row with a nullable date rather than two
    models, because they are the same plan with a deadline attached or not. The
    deadline is the whole difference: without one there is nothing to work back
    from and nothing to count down, so the plan is the topic path in the order
    the student put it in.

    ``days_per_week`` and ``hours_per_week`` are what the student said they can
    manage, not what would be ideal. They are the budget the scheduler is not
    allowed to exceed, which is why they sit on the plan rather than being
    derived from the material.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="plans"
    )
    course = models.OneToOneField(Course, on_delete=models.CASCADE, related_name="plan")
    exam_date = models.DateField(null=True, blank=True)
    days_per_week = models.PositiveSmallIntegerField(default=3)
    hours_per_week = models.PositiveSmallIntegerField(default=6)
    created_at = models.DateTimeField(auto_now_add=True)

    class Mode(models.TextChoices):
        EXAM = "exam", "Exam mode"
        OPEN = "open", "Open mode"

    def __str__(self) -> str:
        return f"{self.course.title}: {self.Mode(self.mode).label}"

    @property
    def mode(self) -> str:
        """Which of the two plans this is, decided by the date and nothing else.

        Derived rather than stored, so there is no second answer on the row to
        fall out of step with the date the student actually gave.
        """
        return self.Mode.EXAM if self.exam_date else self.Mode.OPEN


class Session(models.Model):
    """One study session: one topic, on one day, for a length derived from it.

    A session holds exactly one topic and a topic holds exactly one session.
    Nothing here can express half a topic or two topics in a sitting, which is
    the point: a topic split across days is a topic the student dreads finishing.

    ``position`` is the session's place in the plan as it now stands, counted
    in the order the sessions fall on the calendar. It moves when a rebuild
    shifts work later, because it is a fact about this plan and not about the
    course.

    Which week a session sits in is not stored. It is read off ``scheduled_on``
    when the plan is shown, because a week is a property of a calendar and not
    of a position in a list, and storing it would mean a plan whose first week
    spanned two real ones.

    What the student has done about it is not stored here either: it belongs to
    the topic, because rebuilding the plan replaces every row on this page and
    the student's record of having finished something is not theirs to lose.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sessions"
    )
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="sessions")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="sessions")
    position = models.PositiveIntegerField()
    minutes = models.PositiveSmallIntegerField()
    scheduled_on = models.DateField()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "topic"], name="unique_session_per_topic"
            ),
            models.UniqueConstraint(
                fields=["plan", "position"], name="unique_position_per_plan"
            ),
        ]

    def __str__(self) -> str:
        return f"day {self.position}: {self.topic.title} ({self.minutes} min)"

    @property
    def state(self) -> str:
        """Where this session stands in the student's record.

        A plan holds one session per topic, so this is the topic's answer said
        through the row the student reads it on.
        """
        return self.topic.state

    @property
    def recorded_on(self) -> date | None:
        """The day this session entered the log, whichever way it entered it."""
        return self.topic.recorded_on


class Attempt(models.Model):
    """One time the student took a topic's quiz, and how it went.

    An event log rather than a running score on the topic, because nothing can
    be backfilled: the whole of what mastery is later computed from is the shape
    of these rows over time, and a table invented after the first hundred
    attempts have been taken is a table that starts empty and stays wrong.

    ``score`` is a fraction between zero and one rather than a mark out of any
    number, so that two attempts on two differently sized quizzes can be weighed
    against each other. ``passes`` is how many times the citations the answer
    gave were verified - a second signal from the same sitting, kept because
    separating "knew it" from "got the right answer for the wrong reason" is
    what mastery is later for.

    ``missed`` is the third thing a sitting records: the paragraphs the wrong
    answers came from. Without it the rows cannot say what the student was sent
    to read, and a rule that refuses a retry until the material has been opened
    would have nothing to point at. They are kept as ``AttemptMissed`` rows
    rather than as ids in a column, because a paragraph is a thing the student
    is pointed at and this schema does not keep pointers to things that can be
    deleted underneath them.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attempts"
    )
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="attempts")
    score = models.FloatField()
    passes = models.PositiveIntegerField(default=0)
    missed = models.ManyToManyField(
        Span, through="AttemptMissed", related_name="missed_attempts"
    )
    taken_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["taken_at", "id"]

    def __str__(self) -> str:
        return f"{self.topic.title}: {self.score:.0%} ({self.passes} verified)"

    @property
    def failed(self) -> bool:
        """Whether this sitting left the topic half known or less.

        Read off the score rather than stored, so that the page counting the
        attempts and the row that recorded one cannot answer "was that a
        failure?" differently.
        """
        return is_failure(self.score)


class AttemptMissed(models.Model):
    """One paragraph a sitting sent the student back to.

    Its own row rather than a column of ids on the attempt, for two reasons that
    both point the same way: a paragraph can be deleted without the sitting that
    cited it being wrong, and every table in this schema carries a ``user_id`` so
    that v1 adds a filter rather than a refactor.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attempt_misseds"
    )
    attempt = models.ForeignKey(
        Attempt, on_delete=models.CASCADE, related_name="missed_rows"
    )
    span = models.ForeignKey(
        Span, on_delete=models.CASCADE, related_name="missed_in"
    )

    class Meta:
        ordering = ["span_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "span"], name="unique_missed_span_per_attempt"
            )
        ]

    def __str__(self) -> str:
        return f"attempt {self.attempt_id} missed {self.span_id}"


class SectionOpen(models.Model):
    """One student opening one cited paragraph of the reading surface.

    Presence, not comprehension: opening is enough, and scrolling to the end is
    not required because a rule that can be satisfied by scrolling to the end is
    a rule nobody has to have read anything to pass. The event is cheap because
    the retry rule needs to know the student went back to the material, and that
    is the whole of what it is entitled to ask.

    One row per paragraph, holding the moment they were *last* there. The retry
    rule asks whether they have been back since they were sent, and a first-visit
    timestamp would answer a question nobody asked: it would let a paragraph
    they read before the quiz, and were sent back to after, count as read.

    Kept apart from ``Attempt`` on purpose. An attempt is what the student sat;
    this is what they have looked at since, and it outlives every quiz written
    on the topic, so nothing regenerating a quiz can take it away.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="section_opens"
    )
    span = models.ForeignKey(Span, on_delete=models.CASCADE, related_name="opens")
    opened_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["opened_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "span"], name="unique_open_per_span"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id} opened {self.span_id}"


class Quiz(models.Model):
    """One topic's quiz: the questions that survived, and what was left out.

    A quiz is a row rather than something recomputed on every request, because
    the student has to be able to come back to the same quiz, sit it, and have
    every answer graded against the question they actually read. Generating a
    fresh one is what taking it again does.

    ``dropped`` is kept because a short quiz is the honest outcome rather than a
    failure of the run: the student is told how many questions could not be
    verified against the span they came from and were left out, because a quiz
    that silently got smaller looks like a bug.

    ``low_confidence`` is that same fact judged against the size of the quiz.
    A topic whose material supports one verified question gets one question and
    a flag saying the student cannot treat the score as a measure of what they
    know.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quizzes"
    )
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="quizzes")
    dropped = models.PositiveIntegerField(default=0)
    low_confidence = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.topic.title}: {len(self.questions.all())} questions"

    @property
    def count(self) -> int:
        """How many questions this quiz actually has.

        A property because the count is read on the page, in the attempt and
        in the score, and three readings of a number the student is looking at
        are three chances for them to disagree.
        """
        return self.questions.count()

    @property
    def note(self) -> str:
        """What the student is told about a quiz they should not fully trust."""
        return LOW_CONFIDENCE


class Question(models.Model):
    """One question, and the span it was generated from and cites.

    The span is a foreign key rather than a copy of the paragraph's text,
    because a citation that stores its own text is a citation that can fall out
    of step with the slide it claims to come from. Following the key is the
    whole of what "go and check" means.

    ``answers`` is what counts as right, as text, rather than an index: multiple
    choice keeps exactly one entry and it is one of ``choices``, so the same
    question is graded the same way whichever kind it is, and a short answer can
    accept more than one wording of the same thing. Every entry was checked
    against the span before the question was kept, which is what makes grading
    unable to contradict the source it cites.
    """

    class Kind(models.TextChoices):
        #: The two kinds, and no others. A quiz that needed the student to write
        #: an essay, or to rank, or to draw, could not be graded against a
        #: paragraph without becoming a second opinion about the material rather
        #: than a check on it. These are the strings ``material.model`` is
        #: understood to produce.
        MULTIPLE_CHOICE = "multiple_choice", "Multiple choice"
        SHORT_ANSWER = "short_answer", "Short answer"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="questions"
    )
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    span = models.ForeignKey(Span, on_delete=models.CASCADE, related_name="questions")
    ordinal = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=Kind)
    prompt = models.TextField()
    choices = models.JSONField(default=list)
    answers = models.JSONField(default=list)

    class Meta:
        ordering = ["ordinal", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["quiz", "ordinal"], name="unique_question_ordinal_per_quiz"
            )
        ]

    def __str__(self) -> str:
        return f"{self.quiz_id} q{self.ordinal}: {self.prompt[:40]}"

    @property
    def slide(self) -> Slide:
        """The slide this question came from, named on the page before answering."""
        return self.span.slide

    @property
    def anchor(self) -> str:
        """The exact paragraph a wrong answer sends the student to."""
        return self.span.anchor


class MockExam(models.Model):
    """One exam across the whole course, and the one attempt at it.

    Its own table rather than a quiz hung off a made-up topic, because the exam
    differs from a topic quiz in the three ways that matter and each of those is
    a fact about the row: it spans every topic rather than one, it is sat once
    rather than retaken, and it is scored as a whole rather than folded into a
    topic's record.

    The course is a one-to-one key rather than a foreign key because "one exam,
    one attempt" is the whole rule. A second row would be a second exam, and
    there is no route in the product that creates one - which is why
    ``material.exam`` refuses to write a second rather than trusting the page not
    to ask twice.

    ``taken_on`` is the single attempt, and it is all this row records about it.
    The day is passed in rather than read from the clock so that this table has
    one clock in it too, and it is read by the page offering the paper and by the
    view marking it, so there is one answer to "has this been sat".

    No score is stored beside it. The standing score is marked again from the
    answers on ``MockQuestion`` every time it is shown, so there is no second
    verdict anywhere to fall out of step with the questions it came from.

    ``dropped`` is kept for the same reason a topic quiz keeps it: questions
    written and not shown, which the student is told about rather than left to
    discover.

    ``asked_topics`` is how many topics the course had when the paper was
    written, kept rather than counted off the topics again because the topic
    path is the student's to edit: a topic added this morning would make a paper
    written last month read as though it had left that topic out, and the page
    would be indicting an exam for something it never saw.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mock_exams"
    )
    course = models.OneToOneField(Course, on_delete=models.CASCADE, related_name="exam")
    dropped = models.PositiveIntegerField(default=0)
    asked_topics = models.PositiveIntegerField(default=0)
    taken_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        state = "sat" if self.sat else "not sat"
        return f"{self.course.title}: {len(self.questions.all())} questions, {state}"

    @property
    def sat(self) -> bool:
        """Whether this exam has been sat, and so cannot be sat again.

        Read rather than compared against a date elsewhere, so the page offering
        the paper and the view marking it cannot answer differently.
        """
        return self.taken_on is not None


class MockQuestion(models.Model):
    """One question of the mock exam, the topic it came from, and its span.

    ``topic`` is kept even though the span already says where the question came
    from: an exam covering the whole course is only honest if it can say which
    parts of the course it asked about and which it could not, and a topic with
    no question on it is a fact about this row's absence rather than something
    worked out from spans.

    ``given`` is the answer the student gave, kept so that a result page can be
    re-read after the sitting rather than living only in the response that
    marked it. The mark itself is not stored: it is made again by
    ``material.marking`` from these words, so there is no second verdict to fall
    out of step with the questions.

    ``topic`` is protected rather than cascading. This row is the student's one
    attempt, and deleting a topic from the path they are still allowed to edit
    would otherwise take their paper apart. ``material.topics`` refuses that
    delete and says why.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mock_questions"
    )
    exam = models.ForeignKey(MockExam, on_delete=models.CASCADE, related_name="questions")
    topic = models.ForeignKey(
        Topic,
        on_delete=models.PROTECT,
        related_name="mock_questions",
    )
    span = models.ForeignKey(Span, on_delete=models.CASCADE, related_name="mock_questions")
    ordinal = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=Question.Kind.choices)
    prompt = models.TextField()
    choices = models.JSONField(default=list)
    answers = models.JSONField(default=list)
    given = models.TextField(blank=True)

    class Meta:
        ordering = ["ordinal", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["exam", "ordinal"], name="unique_question_ordinal_per_exam"
            )
        ]

    def __str__(self) -> str:
        return f"{self.exam_id} q{self.ordinal}: {self.prompt[:40]}"

    @property
    def anchor(self) -> str:
        """The exact paragraph a wrong answer sends the student to."""
        return self.span.anchor


class TopicSlide(models.Model):
    """One slide a topic points at.

    A join row rather than a list on the topic, so the pointer map survives
    editing: merging two topics is adding rows, splitting one is deleting them,
    and no edit has to rewrite what a topic covers into a single field.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="topic_slides"
    )
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="pointers")
    slide = models.ForeignKey(
        Slide, on_delete=models.CASCADE, related_name="topic_pointers"
    )

    class Meta:
        ordering = ["slide__source_file_id", "slide__number"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "slide"], name="unique_slide_per_topic"
            )
        ]

    def __str__(self) -> str:
        return f"{self.topic.title} -> {self.slide.anchor}"


class Spend(models.Model):
    """One piece of work the student caused, and what it cost.

    Written by ``material.spend.working`` as the work ends, whether it worked or
    not, because none of this can be backfilled: a cost the product only kept on
    success is a record that says nothing went wrong on the days it did.

    **One row per piece of work, not per model call.** What the product needs to
    price is "this student's course cost 0.4 seconds and no model calls to read
    in, and 1.8 seconds and six calls to write the quiz he has sat", and a row
    per call would make that question an aggregation left to whoever reads the
    table. ``calls`` carries how many the one piece took.

    ``tokens`` is what the provider said its calls moved, and is ``None`` rather
    than zero where the provider said nothing at all: a call that moved nothing
    and a call that would not say are different facts, and only one of them is
    free.

    ``label`` names what the work was for instead of pointing at the topic or the
    file. The path is the student's to edit all semester and a file can be taken
    away, and what a week of generation cost is worth keeping after both of those
    have happened.
    """

    class Kind(models.TextChoices):
        INGEST = "ingest", "Reading material in"
        TOPICS = "topics", "Working out the topic path"
        QUIZ = "quiz", "Writing a topic quiz"
        EXAM = "exam", "Writing the mock exam"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="spends"
    )
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="spends")
    kind = models.CharField(max_length=8, choices=Kind)
    label = models.CharField(max_length=255, blank=True)
    calls = models.PositiveIntegerField(default=0)
    seconds = models.FloatField()
    tokens = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.what}: {self.calls} calls in {self.seconds:.2f}s"

    @property
    def what(self) -> str:
        """What this work was, in the words the page puts it in.

        Both branches are live: reading a file in is named after that file, while
        working out the topic path is named after the course and has no one file
        to point at.
        """
        said = self.get_kind_display()
        return f"{said}: {self.label}" if self.label else said

    @property
    def cost(self) -> float | None:
        """What this piece of work cost in money, or nothing while that is unknown."""
        return price(self.tokens)