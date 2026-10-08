"""What happens when a student uploads course material.

Extraction runs in the request: no queue, no workers. A course is made first and
every file is then attempted on its own, so a file that cannot be read is
recorded as a failure the student can read about instead of costing them the
files beside it. The original is retained alongside the Markdown, and extraction
is cached by content hash so the same bytes are never converted twice.

Each file records the named stages it went through, because a student waiting
on a conversion deserves to know what is happening rather than watching a
spinner: the stages are the same ones the log lines name.
"""

import enum
import hashlib
import logging
import shutil
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from material import ingest
from material.models import Course, Slide, SourceFile, Span
from material.render import plain_text

logger = logging.getLogger(__name__)

NOT_A_PDF = (
    "It is not a PDF file.",
    "Choose a PDF and try again.",
)


class Stage(enum.StrEnum):
    """The named steps an upload goes through, in the order it goes through them."""

    PAGES_READ = "pages_read"
    CONVERTING = "converting"
    VERIFYING = "verifying"

    @property
    def label(self) -> str:
        """The name the student and the log both use for this stage."""
        return _LABELS[self]


_LABELS: dict[Stage, str] = {
    Stage.PAGES_READ: "Reading pages",
    Stage.CONVERTING: "Converting",
    Stage.VERIFYING: "Verifying",
}


class UploadRejected(Exception):
    """The upload cannot be attempted at all, and the student is told why."""


@dataclass(frozen=True)
class StageOutcome:
    """One stage of one file's ingest, as it is recorded and then shown.

    The same value is written onto the file as JSON and read back for the
    reading surface, so it carries the stage's own name and not the label
    students read: the wording can be changed without rewriting rows.
    """

    stage: Stage
    outcome: str
    detail: str

    @property
    def label(self) -> str:
        return self.stage.label

    def as_row(self) -> dict[str, str]:
        return {
            "stage": self.stage.value,
            "outcome": self.outcome,
            "detail": self.detail,
        }

    @classmethod
    def from_row(cls, row: Any) -> "StageOutcome":
        return cls(
            stage=Stage(str(row["stage"])),
            outcome=str(row["outcome"]),
            detail=str(row["detail"]),
        )


@dataclass(frozen=True)
class _Attempt:
    """One file the student chose, before it is known whether it can be read."""

    name: str
    data: bytes
    digest: str

    @classmethod
    def from_upload(cls, upload: UploadedFile[Any]) -> "_Attempt":
        data = upload.read()
        return cls(
            name=Path(str(upload.name)).name,
            data=data,
            digest=file_sha256(data),
        )

    @property
    def is_pdf(self) -> bool:
        return self.data.startswith(b"%PDF")


class _Stages:
    """The stages one file went through, recorded as they are reached.

    A failure keeps the stages that came before it, so the student is shown how
    far the file got rather than only that it stopped.
    """

    def __init__(self, file_name: str) -> None:
        self._name = file_name
        self._open = Stage.PAGES_READ
        self.outcomes: list[StageOutcome] = []

    def start(self, stage: Stage) -> None:
        """Mark the stage now being attempted, so a failure is attributed to it."""
        self._open = stage

    def passed(self, stage: Stage, detail: str) -> None:
        self._open = stage
        self.outcomes.append(StageOutcome(stage, "done", detail))
        logger.info("%s — %s: %s", self._name, stage.label, detail)

    def failed(self, failure: ingest.ConversionError) -> None:
        self.outcomes.append(StageOutcome(self._open, "failed", failure.reason))
        logger.warning("%s — %s: %s", self._name, self._open.label, failure.reason)


def stage_reports(source_file: SourceFile) -> list[StageOutcome]:
    """The stages one file's ingest went through, for the student to read back."""
    return [StageOutcome.from_row(row) for row in source_file.stages]


def _retained_copy(data: bytes, original_name: str) -> str:
    """Store the original under the FileField's own upload_to path."""
    path = default_storage.save(original_name, ContentFile(data))
    return path


@contextmanager
def _uploaded_copy(attempt: _Attempt) -> Iterator[Path]:
    """The uploaded bytes on disk, under the name the student gave them.

    The converter works from a path rather than from bytes, and the names it
    writes extracted images under come from that filename, so the student's own
    name is the one kept.
    """
    directory = Path(tempfile.mkdtemp(prefix="mastory-"))
    path = directory / attempt.name
    path.write_bytes(attempt.data)
    try:
        yield path
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_uploads(
    *, user_id: int, title: str, uploads: Sequence[UploadedFile[Any]]
) -> Course:
    """Ingest several files as one course.

    The course is made first and each file is attempted on its own afterwards,
    so one bad file in a folder of five costs the student that file and not the
    other four. A folder where nothing can be attempted at all is rejected
    before any course exists, because there is nowhere yet to report the failure.
    """
    attempts = [_Attempt.from_upload(upload) for upload in uploads]
    if not any(attempt.is_pdf for attempt in attempts):
        raise UploadRejected(f"{NOT_A_PDF[0]} {NOT_A_PDF[1]}")

    course = Course.objects.create(user_id=user_id, title=title)
    for attempt in attempts:
        _ingest_file(user_id, course, attempt)
    return course


def _ingest_file(user_id: int, course: Course, attempt: _Attempt) -> SourceFile:
    """Ingest one file into ``course``, recording how far it got.

    Its own transaction, and its failures are recorded rather than raised, so
    this file is kept as something the student can be told about.
    """
    stages = _Stages(attempt.name)
    with transaction.atomic():
        source_file = SourceFile.objects.create(
            user_id=user_id,
            course=course,
            original=_retained_copy(attempt.data, attempt.name),
            original_name=attempt.name,
            content_sha256=attempt.digest,
            status=SourceFile.Status.READY,
        )
        failure = _extract(user_id, source_file, attempt, stages)
        if failure is not None:
            source_file.status = SourceFile.Status.FAILED
            source_file.failure_reason = failure.reason
            source_file.failure_advice = failure.advice
        source_file.stages = [outcome.as_row() for outcome in stages.outcomes]
        source_file.save(
            update_fields=[
                "status",
                "failure_reason",
                "failure_advice",
                "page_count",
                "stages",
            ]
        )
    return source_file


@dataclass(frozen=True)
class CachedExtraction:
    source_file: SourceFile
    pages: list[ingest.ConvertedPage]


def _cached_extraction(digest: str, *, excluding: int) -> CachedExtraction | None:
    """Markdown from a previous conversion of the same bytes, if any.

    Scoped by content alone, not by user: one extraction serves every student
    who uploads the same course. A file that failed to convert is never a cache
    hit, or its failure would be copied forward, and neither is the file being
    ingested now, which is only a row until its own conversion has run.
    """
    previous = (
        SourceFile.objects.filter(
            content_sha256=digest, status=SourceFile.Status.READY
        )
        .exclude(pk=excluding)
        .order_by("id")
        .first()
    )
    if previous is None:
        return None
    pages = [
        ingest.ConvertedPage(
            number=slide.number,
            title=slide.title,
            markdown=slide.markdown,
            images=tuple(slide.images),
        )
        for slide in previous.slides.all()
    ]
    return CachedExtraction(previous, pages)


def _extract(
    user_id: int,
    source_file: SourceFile,
    attempt: _Attempt,
    stages: _Stages,
) -> ingest.ConversionError | None:
    """Turn one uploaded file into stored sections, or say why it could not be.

    Returns the failure that stopped it, or ``None`` when it converted.
    """
    deadline = time.monotonic() + settings.INGEST_FILE_TIMEOUT_SECONDS
    cached = (
        _cached_extraction(attempt.digest, excluding=source_file.pk)
        if attempt.is_pdf
        else None
    )
    with _uploaded_copy(attempt) as path:
        try:
            if not attempt.is_pdf:
                raise ingest.ConversionError(*NOT_A_PDF)

            page_count = ingest.count_pages(path)
            source_file.page_count = page_count
            stages.passed(
                Stage.PAGES_READ,
                f"read {page_count} page{'' if page_count == 1 else 's'}",
            )

            stages.start(Stage.CONVERTING)
            pages = _convert(path, cached, source_file, deadline)
            stages.passed(Stage.CONVERTING, _conversion_detail(cached, pages))

            stages.start(Stage.VERIFYING)
            _store_pages(user_id, source_file, pages)
            stages.passed(
                Stage.VERIFYING,
                f"checked {len(pages)} section{'' if len(pages) == 1 else 's'}",
            )
        except ingest.ConversionError as failure:
            stages.failed(failure)
            return failure
    return None


def _convert(
    path: Path,
    cached: CachedExtraction | None,
    source_file: SourceFile,
    deadline: float,
) -> list[ingest.ConvertedPage]:
    """One file's pages as Markdown, from the cache when these bytes were read
    before. Either way the images end up under this file's own directory."""
    if cached is not None:
        pages = cached.pages
        _copy_extracted_images(pages, cached.source_file.image_dir, source_file)
        return pages
    pages = ingest.convert_pdf(
        path, media_root=settings.MEDIA_ROOT, deadline=deadline
    )
    _copy_extracted_images(
        pages, Path(settings.MEDIA_ROOT) / "extracted", source_file
    )
    return pages


def _conversion_detail(
    cached: CachedExtraction | None, pages: list[ingest.ConvertedPage]
) -> str:
    if cached is not None:
        return "reused the conversion made earlier from these exact bytes"
    return f"converted {len(pages)} page{'' if len(pages) == 1 else 's'} to Markdown"


def _store_pages(
    user_id: int, source_file: SourceFile, pages: list[ingest.ConvertedPage]
) -> None:
    for page in pages:
        slide = Slide.objects.create(
            user_id=user_id,
            source_file=source_file,
            number=page.number,
            title=page.title,
            markdown=page.markdown,
            plain_text=plain_text(page.markdown),
            images=list(page.images),
        )
        for ordinal, text in enumerate(_spans(page.markdown)):
            Span.objects.create(
                user_id=user_id, slide=slide, ordinal=ordinal, text=text
            )


def _spans(markdown: str) -> list[str]:
    """The slide's paragraphs, as plain text.

    A span is what a later question is generated from and cited back to, so it
    holds the text a student reads rather than Markdown syntax. Paragraphs are a
    coarse split for now; the topic path work will decide the real boundaries.
    """
    spans = (plain_text(block) for block in markdown.split("\n\n"))
    return [span for span in spans if span]


def _file_image_dir(source_file: SourceFile) -> Path:
    return Path(settings.MEDIA_ROOT) / "slides" / str(source_file.pk)


def _copy_extracted_images(
    pages: list[ingest.ConvertedPage], from_dir: Path, source_file: SourceFile
) -> None:
    """Give each file its own copy of the extracted images.

    The converter writes to one scratch directory, and two courses cannot share
    image names, so each file gets the images under its own directory. The files
    are copied, not moved: an earlier course's images must keep working.
    """
    target = _file_image_dir(source_file)
    target.mkdir(parents=True, exist_ok=True)
    for page in pages:
        for name in page.images:
            path = from_dir / name
            if path.exists():
                shutil.copy2(path, target / name)