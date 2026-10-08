"""PDF to Markdown conversion.

Deterministic by construction: the same PDF always yields the same Markdown and
the same extracted images, and nothing leaves the machine.

Pages are converted one at a time. That costs no more than converting the
document in one go, and it is what lets a file that will not finish be stopped:
the time limit is checked at each page boundary, so a slow PDF is abandoned
rather than left to run.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf
import pymupdf4llm

_IMAGE_LINK = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")
_TITLE_WIDTH = 80

UNREADABLE = (
    "The file could not be opened as a PDF.",
    "Try re-saving it from the app that made it, or splitting the file into "
    "smaller parts and uploading those.",
)
NO_READABLE_TEXT = (
    "There is no readable text anywhere in it, so it looks like pictures of "
    "pages rather than a PDF of text.",
    "Check the pages really have writing on them. Run OCR on the file, or "
    "upload a digital copy with a text layer.",
)
TOO_SLOW = (
    "We stopped converting it because it passed the time limit.",
    "Try splitting the file into smaller PDFs and uploading the parts.",
)


class ConversionError(Exception):
    """A PDF that cannot be turned into material, in words a student can act on.

    ``reason`` says what happened and ``advice`` says what to do about it. Both
    are shown to the student rather than being kept for the log.
    """

    def __init__(self, reason: str, advice: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.advice = advice


@dataclass(frozen=True)
class ConvertedPage:
    number: int
    title: str
    markdown: str
    images: tuple[str, ...]


def count_pages(pdf_path: str | Path) -> int:
    """How many pages this PDF has.

    Opening a file is the first thing a student waits for, so it is a stage of
    its own rather than a detail buried inside conversion.
    """
    try:
        # pymupdf ships a py.typed marker but leaves this callable unannotated.
        with pymupdf.open(str(pdf_path)) as document:  # type: ignore[no-untyped-call]
            return int(document.page_count)
    except Exception as exc:  # pymupdf raises a wide range of types
        raise ConversionError(*UNREADABLE) from exc


def convert_pdf(
    pdf_path: str | Path,
    *,
    media_root: str | Path,
    deadline: float | None = None,
) -> list[ConvertedPage]:
    """Convert a PDF into one Markdown string per page.

    Images are written under ``media_root`` as ``<page>-<n><ext>`` and
    referenced in the Markdown as ``image:<name>``, which the renderer rewrites
    into a URL.

    ``deadline`` is a ``time.monotonic`` reading. Conversion gives up at the
    first page boundary past it, so a file that will not finish stops instead of
    carrying on.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise ConversionError(
            f"No such file: {pdf_path.name}", "Choose the file again."
        )

    image_dir = Path(media_root) / "extracted"
    image_dir.mkdir(parents=True, exist_ok=True)

    chunks: list[dict[str, Any]] = []
    for index in range(count_pages(pdf_path)):
        if deadline is not None and time.monotonic() > deadline:
            raise ConversionError(*TOO_SLOW)
        chunks.extend(_convert_page(pdf_path, image_dir, index))

    pages: list[ConvertedPage] = []
    for chunk in sorted(chunks, key=lambda c: c["metadata"]["page_number"]):
        number: int = chunk["metadata"]["page_number"]
        names = _page_images(image_dir, pdf_path, number)
        markdown = _IMAGE_LINK.sub(
            lambda m: (
                f"![{m['alt']}](image:{names[Path(m['src']).name]})"
                if Path(m["src"]).name in names
                else m.group(0)
            ),
            chunk["text"],
        ).strip()
        pages.append(
            ConvertedPage(
                number=number,
                title=_page_title(markdown, number),
                markdown=markdown,
                images=tuple(names.values()),
            )
        )

    if not any(page.markdown.strip() for page in pages):
        raise ConversionError(*NO_READABLE_TEXT)
    return pages


def _convert_page(
    pdf_path: Path, image_dir: Path, index: int
) -> list[dict[str, Any]]:
    """One page of the document, ``index`` counting from zero as pymupdf does."""
    try:
        chunks: list[dict[str, Any]] = pymupdf4llm.to_markdown(
            str(pdf_path),
            markdown_images=True,
            write_images=True,
            image_path=str(image_dir),
            page_chunks=True,
            pages=[index],
            # A page that is a photograph has no text layer to read, so the
            # converter reads the picture instead of giving up on it. Pages that
            # already carry text are left exactly as they are, which is what
            # keeps a digital PDF converting the same way it always has.
            use_ocr=True,
        )
    except Exception as exc:  # pymupdf raises a wide range of types
        raise ConversionError(*UNREADABLE) from exc
    return chunks


def _page_images(
    image_dir: Path, pdf_path: Path, number: int
) -> dict[str, str]:
    """Rename this page's extracted images to page-scoped names, keyed by the
    name pymupdf wrote them under."""
    written = sorted(image_dir.glob(f"{pdf_path.name}-{number:04d}-*"))
    names: dict[str, str] = {}
    for index, path in enumerate(written):
        target_name = f"{number}-{index:02d}{path.suffix}"
        if path.name != target_name:
            path.replace(image_dir / target_name)
        names[path.name] = target_name
    return names


def _page_title(markdown: str, number: int) -> str:
    """A short, non-empty name for one page.

    Plenty of material has no heading structure to name a page by, so the first
    line stands in for the heading and is cut at a word boundary rather than
    running on for a whole paragraph.
    """
    for line in markdown.splitlines():
        candidate = line.strip().lstrip("#").replace("*", "").strip()
        if candidate:
            return _shorten(candidate)
    return f"Page {number}"


def _shorten(text: str, width: int = _TITLE_WIDTH) -> str:
    if len(text) <= width:
        return text
    head = text[:width].rsplit(" ", 1)[0]
    return f"{head}…" if head else text[:width]