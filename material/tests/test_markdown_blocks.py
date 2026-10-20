"""The reading surface over markdown that spans a blank line.

A loose list and a fenced code block each legally contain a blank line, and a
paragraph split that cut at every blank line used to break both: the list
became several lists and the fences became prose. These tests drive the upload
and the reading page through HTTP the way the rest of the suite does; the one
thing they cannot do for real is convert a PDF into exactly the markdown
under test, so the converter is faked, at the same seam the model tests fake
``material.model.complete``. Everything above the conversion is the real
application: the upload, the stored spans, the anchors, the quiz citation.
"""

import re
from contextlib import contextmanager
from importlib import import_module
from typing import Iterator
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from material import ingest
from material.models import Slide, SourceFile, Span
from material.tests.helpers import (
    LINEAR_ALGEBRA_PDF,
    QuizModel,
    fake_question,
    fake_topic,
    pdf_upload,
    use_fake_model,
    use_temporary_media_root,
)

User = get_user_model()

MIGRATION = "material.migrations.0013_renumber_spans_for_merged_markdown_blocks"

#: A slide with a loose list: blank lines between the items, which is what a
#: paragraph split run over bare chunks once tore into several lists.
LOOSE_LIST = (
    "# Compact form of Av\n"
    "\n"
    "The list below states the whole method.\n"
    "\n"
    "- Turn the matrix into the form the row reduction makes.\n"
    "\n"
    "- Take the remainder as the vector.\n"
    "\n"
    "- Say what the remainder equals.\n"
)

#: A slide whose code block holds a blank line. Under the old split the content
#: below the blank line left the fence, and the closing fence rendered as prose.
FENCED_CODE = (
    "# Reading a table in code\n"
    "\n"
    "The snippet below opens the grades.\n"
    "\n"
    "```python\n"
    "loading = open_path\n"
    "\n"
    "return loading\n"
    "```\n"
)

#: A slide with no paragraph at all: a picture with no words. Nothing to store
#: a span for, and nothing the new split has anything to say about.
IMAGE_ONLY = "![a diagram with no words in it](image:diagram)"


@contextmanager
def converter_for(*pages: str | tuple[str, str]) -> Iterator[None]:
    """Stand in for the PDF converter, answering the markdown written above.

    The uploaded PDF is a real fixture, so page counting runs for real; only
    the conversion itself is replaced, which is the seam the tests cannot get
    from a PDF without engineering one that converts to these exact bytes. A
    page may be markdown alone, or markdown with the title it converts to.
    """

    def convert(
        path: str, *, media_root: str, deadline: float | None = None
    ) -> list[ingest.ConvertedPage]:
        numbered = []
        for index, page in enumerate(pages, start=1):
            if isinstance(page, tuple):
                numbered.append((index, page[0], page[1]))
            else:
                numbered.append((index, page, page.split("\n", 1)[0].lstrip("# ")))
        return [
            ingest.ConvertedPage(
                number=index, title=title, markdown=markdown, images=()
            )
            for index, markdown, title in numbered
        ]

    with mock.patch.object(
        ingest, "convert_pdf", side_effect=convert, name="convert_pdf"
    ):
        yield


class MarkdownBlocksTestCase(TestCase):
    """One course whose pages are the constructs above, uploaded through HTTP."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        with converter_for(LOOSE_LIST, FENCED_CODE, (IMAGE_ONLY, "A picture")):
            self.client.post("/courses/new/", {"files": [pdf_upload()]})

    def read_page(self) -> str:
        response = self.client.get("/courses/1/read/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def section(self, html: str, title: str) -> str:
        """The reading page's markup for the slide titled ``title``."""
        for part in html.split('<section id="')[1:]:
            if f">{title}</h2>" in part:
                return part.split("</section>")[0]
        raise AssertionError(f"no section titled {title!r} on the reading page")

    def span_ids(self, html: str) -> list[str]:
        """The paragraph anchors the page laid out, in order."""
        return re.findall(r'<div class="span" id="([^"]+)"', html)

    # -- a slide with a loose list --------------------------------------------

    def test_a_loose_list_renders_as_one_list_not_several(self) -> None:
        section = self.section(self.read_page(), "Compact form of Av")
        self.assertEqual(section.count("<ul>"), 1)
        self.assertEqual(section.count("<li>"), 3)

    def test_a_loose_list_says_every_item_it_stored(self) -> None:
        html = self.section(self.read_page(), "Compact form of Av")
        body = re.search(r"<ul>.*</ul>", html, re.S)
        assert body is not None, html
        self.assertIn("Turn the matrix", body.group(0))
        self.assertIn("Say what the remainder equals", body.group(0))

    # -- a slide with a fenced code block --------------------------------------

    def test_a_fenced_block_renders_as_code_not_prose(self) -> None:
        section = self.section(self.read_page(), "Reading a table in code")
        self.assertIn("<pre>", section)
        self.assertIn("loading = open_path", section)
        self.assertNotIn("```", section)

    # -- an anchor per paragraph, at the place the spans were numbered -------

    def test_the_reading_page_still_lays_an_anchor_per_paragraph(self) -> None:
        for title in ("Compact form of Av", "Reading a table in code"):
            with self.subTest(slide=title):
                ids = self.span_ids(self.section(self.read_page(), title))
                self.assertEqual(
                    [anchor[-2:] for anchor in ids], ["s0", "s1", "s2"]
                )

    # -- a slide with no spans --------------------------------------------------

    def test_a_slide_with_no_paragraph_is_unchanged_by_the_split(self) -> None:
        section = self.section(self.read_page(), "A picture")
        self.assertEqual(self.span_ids(section), [])
        self.assertIn('<div class="block">', section)


class CitedParagraphTestCase(TestCase):
    """A quiz citation from the list slide still lands on the paragraph it names.

    The list is the third paragraph of its slide - the heading and the sentence
    above it keeping their places - so a question written from it has to cite
    paragraph 3, and its link target has to be the paragraph the reading page
    actually wraps the list in.
    """

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        with converter_for(LOOSE_LIST):
            self.client.post("/courses/new/", {"files": [pdf_upload()]})
        with use_fake_model(fake_topic("The method, as a list", [1])):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/confirm/")

    def quiz_page(self) -> str:
        QuizModel(
            fake_question(1, "What is taken as the vector?", answer="remainder")
        ).use(self)
        response = self.client.get("/courses/1/topics/1/quiz/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def citations(self, html: str) -> dict[int, str]:
        """Per question on the quiz page: the paragraph it cites, and where."""
        cited = {}
        for block in html.split('<li class="question')[1:]:
            number = re.search(r"paragraph (\d+)", block)
            target = re.search(r'#([^"]+)"', block)
            assert number is not None and target is not None, "a page without citations"
            cited[int(number.group(1))] = target.group(1)
        return cited

    def read_page(self) -> str:
        response = self.client.get("/courses/1/read/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def span_ids(self, html: str) -> list[str]:
        return re.findall(r'<div class="span" id="([^"]+)"', html)

    def test_a_quiz_citation_lands_on_the_paragraph_it_names(self) -> None:
        cited = self.citations(self.quiz_page())
        self.assertIn(3, cited, cited)
        anchor = cited[3]
        self.assertTrue(anchor.endswith("-s2"), cited)
        read = self.read_page()
        span_ids = self.span_ids(read)
        self.assertIn(anchor, span_ids, read)
        wrapping = next(
            part.split("</section>")[0]
            for part in read.split('<section id="')[1:]
            if f'id="{anchor}"' in part
        )
        block = next(
            block for block in wrapping.split('<div class="span"')[1:]
            if "Take the remainder as the vector" in block
        )
        self.assertIn("<ul>", block)


class RenumberingMigrationTests(TestCase):
    """The stored rows of a slide whose split changed.

    A ``TestDatabase`` already at the current schema runs this migration's own
    function against rows set up the way the old split stored them, since the
    rows the fix has to move are ones written before the fix existed.
    """

    def slide(self, markdown: str, old_chunks: list[str]) -> Slide:
        from material.render import plain_text

        user = User.objects.create(username=f"user-{len(User.objects.all())}")
        course = __import__("material.models", fromlist=["Course"]).Course.objects.create(
            user=user, title="Course"
        )
        source_file = SourceFile.objects.create(
            user=user, course=course, original="originals/slide.pdf",
            original_name="slide.pdf", content_sha256=f"digest-{markdown[:20]}",
        )
        slide = Slide.objects.create(
            user=user, source_file=source_file, number=1, title="Slide",
            markdown=markdown,
        )
        Span.objects.bulk_create(
            Span(user=user, slide=slide, ordinal=ordinal, text=plain_text(chunk))
            for ordinal, chunk in enumerate(old_chunks)
        )
        return slide

    def _ordinal_texts(self, slide: Slide) -> list[tuple[int, str]]:
        return sorted(
            (span.ordinal, span.text) for span in slide.spans.order_by("id")
        )

    def _renumber(self) -> None:
        import_module(MIGRATION).renumber(apps, None)

    def test_a_slide_with_a_loose_list_is_renumbered_onto_the_new_split(self) -> None:
        old = [chunk for chunk in LOOSE_LIST.split("\n\n") if chunk.strip()]
        slide = self.slide(LOOSE_LIST, old)
        heading = slide.spans.order_by("id")[0]
        from material.models import SectionOpen

        read = SectionOpen.objects.create(user=heading.user, span=heading)
        self._renumber()
        texts = self._ordinal_texts(slide)
        self.assertIn(
            (
                2,
                "- Turn the matrix into the form the row reduction makes. "
                "- Take the remainder as the vector. "
                "- Say what the remainder equals.",
            ),
            texts,
        )
        # The heading span survived the split: the row and everything that
        # cites it keep their identity, only the ordinal moved.
        read.refresh_from_db()
        self.assertEqual(read.span_id, heading.pk)
        heading.refresh_from_db()
        self.assertEqual(heading.ordinal, 0)

    def test_a_slide_with_neither_construct_keeps_its_rows_and_ids(self) -> None:
        plain = "# A heading\n\nOrdinary paragraph.\n\nAnother ordinary paragraph."
        old = plain.split("\n\n")
        slide = self.slide(plain, old)
        before = list(slide.spans.order_by("id"))
        self._renumber()
        after = list(slide.spans.order_by("id"))
        self.assertEqual([span.pk for span in after], [span.pk for span in before])
        self.assertEqual(
            self._ordinal_texts(slide),
            [(ordinal, span.text) for ordinal, span in enumerate(before)],
        )
