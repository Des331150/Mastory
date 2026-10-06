"""What happens when a student uploads a PDF.

Extraction runs in the request: no queue, no workers. The original is retained
alongside the Markdown, and extraction is cached by content hash so the same
bytes are never converted twice.
"""

import hashlib
import logging
import shutil
import tempfile
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


class UploadRejected(Exception):
    """The upload cannot be attempted at all, and the student is told why."""


@dataclass(frozen=True)
class IngestReport:
    course: Course
    source_file: SourceFile
    reused_extraction: bool


def _retained_copy(data: bytes, original_name: str) -> str:
    """Store the original under the FileField's own upload_to path."""
    path = default_storage.save(original_name, ContentFile(data))
    return path


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_upload(
    *, user_id: int, title: str, uploaded: UploadedFile[Any], original_name: str
) -> IngestReport:
    data = uploaded.read()
    if not data.startswith(b"%PDF"):
        raise UploadRejected("That file is not a PDF. Choose a PDF and try again.")

    digest = file_sha256(data)
    cached = _cached_extraction(digest)

    with transaction.atomic():
        course = Course.objects.create(user_id=user_id, title=title)
        source_file = SourceFile.objects.create(
            user_id=user_id,
            course=course,
            original=_retained_copy(data, original_name),
            original_name=original_name,
            content_sha256=digest,
            status=SourceFile.Status.READY,
        )

        if cached is None:
            logger.info("reading pages of %s", original_name)
            pages = _convert(data, original_name, source_file)
            source_file.page_count = len(pages)
            source_file.save(update_fields=["page_count"])
            _store_pages(user_id, source_file, pages)
            reused = False
        else:
            logger.info("reusing extraction cached for %s", original_name)
            pages = cached.pages
            _copy_extracted_images(pages, cached.source_file.image_dir, source_file)
            _store_pages(user_id, source_file, pages)
            source_file.page_count = len(pages)
            source_file.save(update_fields=["page_count"])
            reused = True
        logger.info("source verified for %s", original_name)

    return IngestReport(course, source_file, reused)


def _convert(
    data: bytes, original_name: str, source_file: SourceFile
) -> list[ingest.ConvertedPage]:
    directory = Path(tempfile.mkdtemp(prefix="mastory-"))
    path = directory / original_name
    path.write_bytes(data)
    try:
        pages = ingest.convert_pdf(path, media_root=settings.MEDIA_ROOT)
    except ingest.ConversionError as exc:
        logger.warning("could not read %s: %s", original_name, exc)
        source_file.status = SourceFile.Status.FAILED
        source_file.failure_reason = str(exc)
        source_file.save(update_fields=["status", "failure_reason"])
        return []
    _copy_extracted_images(
        pages, Path(settings.MEDIA_ROOT) / "extracted", source_file
    )
    return pages


@dataclass(frozen=True)
class CachedExtraction:
    source_file: SourceFile
    pages: list[ingest.ConvertedPage]


def _cached_extraction(digest: str) -> CachedExtraction | None:
    """Markdown from a previous conversion of the same bytes, if any.

    Scoped by content alone, not by user: one extraction serves every student
    who uploads the same course. A file that failed to convert is never a cache
    hit, or its failure would be copied forward.
    """
    previous = (
        SourceFile.objects.filter(
            content_sha256=digest, status=SourceFile.Status.READY
        )
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