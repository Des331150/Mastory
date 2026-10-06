"""Shared helpers for the HTTP-seam tests.

Every test drives the real Django application through the test client, so the
helpers here stay close to what a student does: POST a PDF, read the response.
"""

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

FIXTURES = Path(__file__).resolve().parent / "fixtures"

LINEAR_ALGEBRA_PDF = FIXTURES / "math-201-eigenvalues.pdf"
CELL_BIOLOGY_PDF = FIXTURES / "bio-110-membrane-transport.pdf"


def pdf_upload(
    path: Path = LINEAR_ALGEBRA_PDF, name: str | None = None
) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name or path.name, path.read_bytes(), content_type="application/pdf"
    )


@contextmanager
def temporary_media_root() -> Iterator[Path]:
    """Point MEDIA_ROOT at a scratch directory so tests leave nothing behind."""
    with TemporaryDirectory() as directory:
        with override_settings(MEDIA_ROOT=Path(directory)):
            yield Path(directory)


def use_temporary_media_root(test_case: TestCase) -> None:
    """Give a TestCase a scratch MEDIA_ROOT for the duration of each test."""
    manager = temporary_media_root()
    manager.__enter__()
    test_case.addCleanup(manager.__exit__, None, None, None)

