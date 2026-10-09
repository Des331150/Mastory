"""The extraction layer: Course -> File -> Slide -> Span, and the pointer map
Course -> Topic -> TopicSlide.

Every table carries a ``user_id`` so that adding real authentication in v1 is a
filter rather than a refactor.
"""

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
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="topics"
    )
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="topics")
    title = models.CharField(max_length=255)
    position = models.PositiveIntegerField(default=0)
    weight = models.PositiveIntegerField(default=1)
    flagged = models.BooleanField(default=False)
    flag_note = models.TextField(blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "position"], name="unique_topic_position_per_course"
            )
        ]

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