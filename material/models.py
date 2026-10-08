"""The extraction layer: Course -> File -> Slide -> Span.

Every table carries a ``user_id`` so that adding real authentication in v1 is a
filter rather than a refactor.
"""

from pathlib import Path

from django.conf import settings
from django.db import models


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