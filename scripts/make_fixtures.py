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


def _uniform(doc: pymupdf.Document, lines: list[str]) -> pymupdf.Page:
    """A page with no heading structure at all.

    One font, one size, one leading, every line left-aligned at the same margin:
    there is no line a converter could pick out as a heading, which is how
    lecture handouts, transcripts and pasted print-outs actually look.
    """
    page = doc.new_page(width=595, height=842)
    y = TOP
    for line in lines:
        for part in _wrap(line, 92):
            page.insert_text((LEFT, y), part, fontsize=BODY, fontname="helv")
            y += 16
    return page


def _rasterised(lines: list[str]) -> pymupdf.Pixmap:
    """Text drawn, then flattened to pixels: a page with no text layer.

    This is what a scanner or a phone camera produces, and it is the shape of
    material that can only be read by looking at the picture.
    """
    scratch = pymupdf.open()
    page = _uniform(scratch, lines)
    pixmap = page.get_pixmap(dpi=150)
    scratch.close()
    return pixmap


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


def handout_notes() -> Path:
    """A handout with no heading structure: uniform type, no titles."""
    path = FIXTURES / "che-212-handout-notes.pdf"
    doc = pymupdf.open()
    _uniform(
        doc,
        [
            "Week 4 tutorial. Write the curved-arrow mechanism for every step below.",
            "Reagent 1 is sodium borohydride in methanol at 0 degrees. Reagent 2 is "
            "the acid workup, added dropwise after the reaction has finished.",
            "Draw the tetrahedral intermediate and say why the hydride attacks from "
            "the less hindered face of the carbonyl.",
        ],
    )
    _uniform(
        doc,
        [
            "Question 3. Assign CIP priorities at each stereocentre in the product "
            "below and state the configuration you get.",
            "Question 4. Explain why the aldehyde is more electrophilic than the "
            "ketone on the previous page, using orbital arguments rather than "
            "steric ones.",
            "Question 5. One mark each. Which of these reactions is a redox reaction, "
            "which is a substitution, and which is an addition-elimination?",
        ],
    )
    _uniform(
        doc,
        [
            "Marking note from the demonstrator. Full marks are for the intermediate "
            "and the stereochemical outcome together, not for the arrow pushing on "
            "its own.",
            "Further reading. Clayden, chapter 6, and the lecture recording from "
            "Tuesday is on the course page under past papers.",
            "Next week: nucleophilic acyl substitution. Read ahead before the "
            "seminar because we start from the mechanisms you did here.",
        ],
    )
    doc.save(str(path))
    return path


def handout_scan() -> Path:
    """A scan of the same handout: pictures of pages, and no text layer."""
    path = FIXTURES / "che-212-handout-scan.pdf"
    doc = pymupdf.open()
    pictures = [
        _rasterised(
            [
                "Week 4 tutorial, scanned copy",
                "Reagent 1 is sodium borohydride in methanol at 0 degrees.",
                "Reagent 2 is the acid workup, added dropwise afterwards.",
            ]
        ),
        _rasterised(
            [
                "Question 3, scanned copy",
                "Assign CIP priorities at each stereocentre in the product.",
                "Question 4. Why is the aldehyde more electrophilic?",
            ]
        ),
    ]
    for pixmap in pictures:
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, pixmap=pixmap)
    doc.save(str(path))
    return path


def blank_scan() -> Path:
    """A scan with pictures of pages and nothing written on them.

    The back of a sheet, or a fold-out that came out blank. There is nothing on
    these pages for any OCR engine to read, which is what makes them the
    honest test of what happens when a scan yields no text at all.
    """
    path = FIXTURES / "che-212-blank-scan.pdf"
    doc = pymupdf.open()
    scratch = pymupdf.open()
    page = scratch.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(28, 28, 567, 814), color=(0.72, 0.72, 0.72), width=1)
    page.draw_line(pymupdf.Point(28, 28), pymupdf.Point(567, 814), color=(0.86, 0.86, 0.86), width=1)
    pixmap = page.get_pixmap(dpi=150)
    scratch.close()
    for _ in range(2):
        scanned = doc.new_page(width=595, height=842)
        scanned.insert_image(scanned.rect, pixmap=pixmap)
    doc.save(str(path))
    return path


def lecture_deck() -> Path:
    """A digital deck whose second slide is a scan: OCR is needed for one page.

    The first page has an ordinary text layer, so the file reads as a digital
    PDF to anything that looks at the whole document. The second page is a
    picture of a slide, which is what forces the converter to fall back on OCR.
    """
    path = FIXTURES / "che-212-lecture-deck.pdf"
    doc = pymupdf.open()
    _page(
        doc,
        "CHE 212: Organic Chemistry",
        [
            "Department of Chemistry, University of Ghana. Lecturer: Dr. M. Boateng.",
            "Lecture 9. Carbonyl chemistry: nucleophilic addition.",
            "Prerequisites: orbital hybridisation, resonance, curved-arrow notation.",
        ],
    )
    scanned = _rasterised(
        [
            "The carbonyl carbon carries a partial positive charge.",
            "Nucleophiles attack this carbon; the pi electrons move to oxygen.",
            "Protonation of the alkoxide in the workup gives the alcohol.",
        ]
    )
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, pixmap=scanned)
    doc.save(str(path))
    return path


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for build in (
        linear_algebra,
        cell_biology,
        handout_notes,
        handout_scan,
        blank_scan,
        lecture_deck,
    ):
        print(f"wrote {build()}")


if __name__ == "__main__":
    main()