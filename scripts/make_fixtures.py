"""Regenerate the committed course PDF fixtures.

The fixtures are committed so the test suite depends on real course slides
rather than on a synthetic PDF written at test time. Run with:

    python scripts/make_fixtures.py
"""

from pathlib import Path

import pymupdf

FIXTURES = Path(__file__).resolve().parent.parent / "material" / "tests" / "fixtures"

TITLE = 19
BODY = 11.5
LEFT = 62
TOP = 74


def _page(doc: pymupdf.Document, heading: str, blocks: list[str]) -> pymupdf.Page:
    page = doc.new_page(width=595, height=842)
    page.insert_text((LEFT, TOP), heading, fontsize=TITLE, fontname="hebo")
    y = TOP + 34
    for block in blocks:
        for line in _wrap(block, 88):
            page.insert_text((LEFT, y), line, fontsize=BODY, fontname="helv")
            y += 16
        y += 6
    return page


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _diagram(
    doc: pymupdf.Document,
    heading: str,
    blocks: list[str],
    boxes: list[tuple[str, str]],
) -> pymupdf.Page:
    page = _page(doc, heading, blocks)
    top = TOP + 34 + sum(len(_wrap(b, 88)) for b in blocks) * 16 + 30
    # The diagram is rasterised and embedded as an image rather than drawn as
    # vectors, so the PDF carries a real image object for the converter to
    # extract. Real lecture decks carry rasterised figures too.
    scratch = pymupdf.open()
    scratch_page = scratch.new_page(width=460, height=70)
    for i, (label, next_label) in enumerate(boxes):
        x = 20 + i * 200
        scratch_page.draw_rect(
            pymupdf.Rect(x, 12, x + 140, 58), color=(0.2, 0.3, 0.6), fill=(0.88, 0.91, 0.98)
        )
        scratch_page.insert_text((x + 16, 40), label, fontsize=13, fontname="hebo")
        if next_label:
            scratch_page.draw_line(
                pymupdf.Point(x + 140, 35), pymupdf.Point(x + 200, 35), width=2
            )
    pixmap = scratch_page.get_pixmap(dpi=150)
    page.insert_image(
        pymupdf.Rect(LEFT, top, LEFT + 460, top + 70), pixmap=pixmap
    )
    scratch.close()
    return page



def linear_algebra() -> Path:
    path = FIXTURES / "math-201-eigenvalues.pdf"
    doc = pymupdf.open()
    _page(
        doc,
        "MATH 201: Linear Algebra",
        [
            "Department of Mathematics, University of Ghana. Lecturer: Dr. K. Osei-Mensah.",
            "Lecture 3 of 12. Eigenvalues and eigenvectors.",
            "Prerequisites: determinants, systems of linear equations, vector spaces.",
        ],
    )
    _page(
        doc,
        "Definition of an eigenvalue",
        [
            "Let A be a square n by n matrix over the reals. A scalar lambda is an "
            "eigenvalue of A if there exists a non-zero vector v such that Av = lambda v.",
            "The vector v is called an eigenvector of A corresponding to lambda.",
            "The zero vector is never an eigenvector, so an eigenvalue of 0 is still "
            "meaningful.",
            "Worked example: for A = [[2, 1], [0, 2]] the characteristic polynomial is "
            "(2 - lambda)^2, so lambda = 2 is the only eigenvalue.",
        ],
    )
    _diagram(
        doc,
        "Computing the characteristic polynomial",
        [
            "The characteristic polynomial of A is p(lambda) = det(A - lambda I).",
            "Set p(lambda) = 0 and solve for lambda over the reals.",
            "The spectrum of A is the set of its eigenvalues, with multiplicity.",
        ],
        [("det(A - L I)", "p(L) = 0"), ("solve", "spectrum")],
    )
    _page(
        doc,
        "Eigenvectors and eigenspaces",
        [
            "For each eigenvalue lambda the eigenspace is the null space of A - lambda I.",
            "The geometric multiplicity is dim(ker(A - lambda I)) and the algebraic "
            "multiplicity is the multiplicity of lambda as a root of p.",
            "Diagonalisation of A is possible exactly when every eigenvalue has equal "
            "geometric and algebraic multiplicity.",
            "Exam note: questions 3 and 7 of the 2023 paper ask for an eigenspace basis.",
        ],
    )
    _page(
        doc,
        "Diagonalisation worked example",
        [
            "Take A = [[4, 1], [2, 3]]. det(A - lambda I) = (4 - lambda)(3 - lambda) - 2 "
            "= lambda^2 - 7 lambda + 10.",
            "The roots are lambda = 5 and lambda = 2, so A has two distinct eigenvalues.",
            "For lambda = 5 the null space of A - 5I is spanned by (1, 1).",
            "For lambda = 2 the null space of A - 2I is spanned by (1, -2).",
            "Hence P = [[1, 1], [1, -2]] and P inverse A P = diag(5, 2).",
        ],
    )
    doc.save(str(path))
    return path


def cell_biology() -> Path:
    path = FIXTURES / "bio-110-membrane-transport.pdf"
    doc = pymupdf.open()
    _page(
        doc,
        "BIO 110: Cell Biology",
        [
            "Department of Zoology, University of Ghana. Lecturer: Prof. A. Aboagye.",
            "Lecture 5. Membrane transport in the eukaryotic cell.",
            "Prerequisites: phospholipid structure, fluid mosaic model.",
        ],
    )
    _page(
        doc,
        "The plasma membrane as a barrier",
        [
            "The plasma membrane is a phospholipid bilayer roughly 7 nm thick.",
            "Hydrophobic interiors make the bilayer semipermeable: small non-polar "
            "molecules cross freely, ions do not.",
            "Integral proteins span the bilayer and provide selective channels.",
        ],
    )
    _page(
        doc,
        "Passive transport",
        [
            "Simple diffusion moves a solute down its concentration gradient and "
            "consumes no ATP.",
            "Facilitated diffusion moves a solute down its gradient through a channel "
            "or carrier protein.",
            "Osmosis is the movement of water across a semipermeable membrane from low "
            "solute concentration to high solute concentration.",
            "In a red blood cell placed in distilled water, water enters by osmosis and "
            "the cell lyses (haemolysis).",
        ],
    )
    _diagram(
        doc,
        "Active transport and the sodium-potassium pump",
        [
            "Active transport moves a solute against its gradient and requires energy.",
            "The sodium-potassium pump exports three sodium ions and imports two "
            "potassium ions per ATP hydrolysed, so the pump is electrogenic.",
            "Inhibition by ouabain stops the pump and the cell swells.",
        ],
        [("3 Na+ out", "2 K+ in"), ("ATP", "gradient")],
    )
    _page(
        doc,
        "Summary table",
        [
            "Simple diffusion: down gradient, no protein, no ATP.",
            "Facilitated diffusion: down gradient, protein, no ATP.",
            "Primary active transport: against gradient, protein, ATP.",
            "Endocytosis and exocytosis: bulk transport of vesicles, ATP required.",
            "Tutorial question: explain why a plant cell in distilled water does not "
            "lyse as readily as an animal cell.",
        ],
    )
    doc.save(str(path))
    return path


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for build in (linear_algebra, cell_biology):
        print(f"wrote {build()}")


if __name__ == "__main__":
    main()