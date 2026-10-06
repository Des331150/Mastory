"""PDF to Markdown conversion.

Deterministic by construction: no model is called, no network is touched, and
the same PDF always yields the same Markdown and the same extracted images.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf4llm

_IMAGE_LINK = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")


class ConversionError(Exception):
    """Raised when a PDF cannot be read at all."""


@dataclass(frozen=True)
class ConvertedPage:
    number: int
    title: str
    markdown: str
    images: tuple[str, ...]


def convert_pdf(
    pdf_path: str | Path, *, media_root: str | Path
) -> list[ConvertedPage]:
    """Convert a PDF into one Markdown string per page.

    Images are written under ``media_root`` as ``<page>-<n><ext>`` and
    referenced in the Markdown as ``image:<name>``, which the renderer rewrites
    into a URL.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise ConversionError(f"No such file: {pdf_path.name}")

    image_dir = Path(media_root) / "extracted"
    image_dir.mkdir(parents=True, exist_ok=True)

    try:
        chunks: list[dict[str, Any]] = pymupdf4llm.to_markdown(
            str(pdf_path),
            markdown_images=True,
            write_images=True,
            image_path=str(image_dir),
            page_chunks=True,
        )
    except Exception as exc:  # pymupdf raises a wide range of types
        raise ConversionError(str(exc)) from exc

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
    return pages


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
    for line in markdown.splitlines():
        candidate = line.strip().lstrip("#").replace("*", "").strip()
        if candidate:
            return candidate[:255]
    return f"Page {number}"