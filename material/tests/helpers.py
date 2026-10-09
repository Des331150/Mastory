"""Shared helpers for the HTTP-seam tests.

Every test drives the real Django application through the test client, so the
helpers here stay close to what a student does: POST a PDF, read the response.

The one thing a test cannot do for real is talk to a model. ``use_fake_model``
installs a deterministic answer at ``material.model.complete``, the single
function Mastory leaves the machine through, so every test above the fake is the
real application.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from material import model

#: One topic as the fake model claims it: a title, the slides it cites by their
#: course position, and how sure it is.
Claim = dict[str, Any]

FIXTURES = Path(__file__).resolve().parent / "fixtures"

LINEAR_ALGEBRA_PDF = FIXTURES / "math-201-eigenvalues.pdf"
CELL_BIOLOGY_PDF = FIXTURES / "bio-110-membrane-transport.pdf"
HANDOUT_NOTES_PDF = FIXTURES / "che-212-handout-notes.pdf"
HANDOUT_SCAN_PDF = FIXTURES / "che-212-handout-scan.pdf"
BLANK_SCAN_PDF = FIXTURES / "che-212-blank-scan.pdf"
LECTURE_DECK_PDF = FIXTURES / "che-212-lecture-deck.pdf"


def pdf_upload(
    path: Path = LINEAR_ALGEBRA_PDF, name: str | None = None
) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name or path.name, path.read_bytes(), content_type="application/pdf"
    )


def pdf_uploads(*paths: Path) -> list[SimpleUploadedFile]:
    """Several files chosen together, as a multi-file upload sends them."""
    return [pdf_upload(path) for path in paths]


def ocr_is_available() -> bool:
    """Whether this machine can read text out of a picture.

    Reading a scanned page needs Tesseract's data files, which are not part of
    the project dependencies. A test that needs the words back says so rather
    than passing on an empty page.
    """
    import pymupdf

    try:
        pymupdf.get_tessdata()  # type: ignore[no-untyped-call]
    except Exception:
        return False
    return True


def broken_pdf_upload(name: str = "corrupt-deck.pdf") -> SimpleUploadedFile:
    """A file that claims to be a PDF and is not one."""
    return SimpleUploadedFile(
        name, b"%PDF-1.7\nthis is not really a pdf", content_type="application/pdf"
    )


def not_a_pdf_upload(name: str = "notes.txt") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"just some notes", content_type="text/plain")


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


def fake_topic(title: str, slides: list[int], confidence: float = 0.9) -> Claim:
    """One topic the fake model claims, citing slides by their course position."""
    return {"title": title, "slides": slides, "confidence": confidence}


def use_fake_model(*topics: Claim, reply: str | None = None) -> Any:
    """Answer every model call in this test with these topics.

    Returns the patcher so it can be used as a context manager or started with
    ``addCleanup``. The application above the fake is entirely real: the reply
    still has to be grounded in the material, because that happens in
    ``material.model`` rather than here.
    """
    answer = reply if reply is not None else json.dumps({"topics": list(topics)})
    patcher = mock.patch.object(model, "complete", return_value=answer, name="complete")
    return patcher

