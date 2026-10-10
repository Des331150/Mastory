"""The extraction layer: Course -> File -> Slide -> Span, the pointer map
Course -> Topic -> TopicSlide, the plan Course -> Plan -> Session, and the record
the student keeps on a topic: ``Topic.completed_on`` and the per-topic ``Attempt``.

Every table carries a ``user_id`` so that adding real authentication in v1 is a
filter rather than a refactor.
"""

from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import models

from material.users import owned


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
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attempts"
    )
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="attempts")
    score = models.FloatField()
    passes = models.PositiveIntegerField(default=0)
    taken_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["taken_at", "id"]

    def __str__(self) -> str:
        return f"{self.topic.title}: {self.score:.0%} ({self.passes} verified)"


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