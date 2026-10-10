"""Ingest for messy, multi-file material, driven through the HTTP seam.

A student's material is not one tidy deck. It is five PDFs in a folder, one of
which came off a phone camera, and one of which will not open at all. Every
assertion here is about what the student sees after uploading such a folder and
reading what came back.
"""

import re
import unittest
from typing import Any

from django.test import TestCase, override_settings

from material.tests.helpers import (
    BLANK_SCAN_PDF,
    CELL_BIOLOGY_PDF,
    HANDOUT_NOTES_PDF,
    HANDOUT_SCAN_PDF,
    LECTURE_DECK_PDF,
    LINEAR_ALGEBRA_PDF,
    broken_pdf_upload,
    not_a_pdf_upload,
    ocr_is_available,
    pdf_upload,
    pdf_uploads,
    use_temporary_media_root,
)


class IngestTestCase(TestCase):
    """A scratch MEDIA_ROOT and nothing uploaded yet."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)

    def upload(self, *uploads: Any, title: str = "") -> Any:
        return self.client.post("/courses/new/", {"title": title, "files": uploads})

    def read(self, course_pk: int = 1, query: str = "") -> str:
        response = self.client.get(f"/courses/{course_pk}/read/?q={query}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def stages_of(self, html: str) -> list[tuple[str, str, str]]:
        """Every stage the student is shown: its name, how it went, and what it did."""
        shown = []
        found = re.findall(r'<li class="stage (\w+)">(.*?)</li>', html, re.S)
        for outcome, body in found:
            label, _, detail = body.partition(":")
            shown.append((label.strip(), outcome, detail.strip()))
        return shown

    def stage_names(self, html: str) -> list[str]:
        return [name for name, _, _ in self.stages_of(html)]


def stream_bytes(response: Any) -> bytes:
    """The bytes a streaming response sent, whatever the test client wrapped."""
    return b"".join(response.streaming_content)


class SeveralFilesOneCourseTests(IngestTestCase):
    def test_three_files_uploaded_at_once_are_read_from_one_course(self) -> None:
        self.upload(
            *pdf_uploads(LINEAR_ALGEBRA_PDF, CELL_BIOLOGY_PDF, HANDOUT_NOTES_PDF),
            title="Mixed year",
        )
        html = self.read()
        self.assertIn("Eigenvalues and eigenvectors", html)
        self.assertIn("plasma membrane", html)
        self.assertIn("sodium borohydride", html)

    def test_the_three_files_are_one_course_rather_than_three(self) -> None:
        self.upload(
            *pdf_uploads(LINEAR_ALGEBRA_PDF, CELL_BIOLOGY_PDF, HANDOUT_NOTES_PDF)
        )
        listing = self.client.get("/").content.decode()
        self.assertEqual(len(re.findall(r'href="/courses/\d+/read/"', listing)), 1)
        self.assertIn("3 files", listing)

    def test_every_file_of_the_course_stays_one_click_from_the_material(self) -> None:
        self.upload(
            *pdf_uploads(LINEAR_ALGEBRA_PDF, CELL_BIOLOGY_PDF, HANDOUT_NOTES_PDF)
        )
        html = self.read()
        names = (
            "math-201-eigenvalues.pdf",
            "bio-110-membrane-transport.pdf",
            "che-212-handout-notes.pdf",
        )
        for name in names:
            with self.subTest(file=name):
                self.assertIn(name, html)
        for file_id in (1, 2, 3):
            with self.subTest(file_id=file_id):
                self.assertEqual(
                    self.client.get(f"/courses/1/original/{file_id}/").status_code, 200
                )

    def test_one_pdf_uploaded_alone_still_makes_one_course(self) -> None:
        self.upload(pdf_upload())
        self.assertIn("Eigenvalues", self.read())


class HeadinglessFileTests(IngestTestCase):
    def test_a_handout_with_no_headings_at_all_ingests_and_reads(self) -> None:
        self.upload(pdf_upload(HANDOUT_NOTES_PDF))
        html = self.read()
        self.assertIn("sodium borohydride", html)
        self.assertIn("Assign CIP priorities", html)
        self.assertIn("Marking note from the demonstrator", html)

    def test_every_page_of_a_headingless_file_is_readable(self) -> None:
        self.upload(pdf_upload(HANDOUT_NOTES_PDF))
        self.assertEqual(self.read().count("<section"), 3)

    def test_each_page_of_a_headingless_file_gets_a_title_the_student_can_use(self) -> None:
        self.upload(pdf_upload(HANDOUT_NOTES_PDF))
        index = re.findall(r'<li>\s*<a href="#[^"]*">(.*?)</a>', self.read())
        self.assertEqual(len(index), 3)
        for title in index:
            with self.subTest(title=title):
                self.assertTrue(title.strip())
                self.assertLessEqual(len(title), 80)

    def test_the_index_links_still_work_for_a_headingless_file(self) -> None:
        self.upload(pdf_upload(HANDOUT_NOTES_PDF))
        html = self.read()
        target = re.search(r'href="#(.*?)"', html)
        assert target is not None
        self.assertIn(f'<section id="{target.group(1)}"', html)


class ScanTests(IngestTestCase):
    def test_a_digital_deck_whose_slide_is_a_scan_converts_successfully(self) -> None:
        self.upload(pdf_upload(LECTURE_DECK_PDF))
        html = self.read()
        self.assertNotIn("could not be converted", html)
        self.assertIn("nucleophilic addition", html)
        self.assertEqual(html.count("<section"), 2)

    @unittest.skipUnless(
        ocr_is_available(), "reading a scanned page needs Tesseract's data files"
    )
    def test_the_scanned_slide_of_a_digital_deck_is_read_back(self) -> None:
        self.upload(pdf_upload(LECTURE_DECK_PDF))
        html = self.read()
        self.assertIn("partial positive charge", html)

    @unittest.skipUnless(
        ocr_is_available(), "reading a scanned page needs Tesseract's data files"
    )
    def test_a_scan_of_real_handwriting_converts_rather_than_being_rejected(self) -> None:
        self.upload(pdf_upload(HANDOUT_SCAN_PDF))
        html = self.read()
        self.assertNotIn("could not be converted", html)
        self.assertIn("sodium borohydride", html)

    def test_a_scan_with_nothing_readable_on_it_is_rejected(self) -> None:
        self.upload(pdf_upload(BLANK_SCAN_PDF))
        html = self.read()
        self.assertIn("che-212-blank-scan.pdf could not be converted", html)
        self.assertIn("no readable text", html)
        self.assertIn("Run OCR", html)

    def test_the_rejection_says_how_many_pages_were_read(self) -> None:
        self.upload(pdf_upload(BLANK_SCAN_PDF))
        self.assertIn("read 2 pages", self.stages_of(self.read())[0][2])

    def test_a_rejected_scan_still_offers_the_original_back(self) -> None:
        self.upload(pdf_upload(BLANK_SCAN_PDF))
        response = self.client.get("/courses/1/original/1/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stream_bytes(response), BLANK_SCAN_PDF.read_bytes())


class NamedStageTests(IngestTestCase):
    def test_the_student_is_shown_the_named_stages_before_the_upload_starts(self) -> None:
        html = self.client.get("/").content.decode()
        for stage in ("Reading pages", "Converting", "Verifying"):
            with self.subTest(stage=stage):
                self.assertIn(stage, html)

    def test_the_student_sees_the_stages_the_file_went_through(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF))
        shown = self.stages_of(self.read())
        self.assertEqual(
            [(name, outcome) for name, outcome, _ in shown],
            [
                ("Reading pages", "done"),
                ("Converting", "done"),
                ("Verifying", "done"),
            ],
        )

    def test_each_stage_says_what_it_did(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF))
        details = [detail for _, _, detail in self.stages_of(self.read())]
        self.assertIn("5 pages", details[0])
        self.assertIn("5 sections", details[2])

    def test_a_file_that_stops_shows_the_stage_it_stopped_at(self) -> None:
        self.upload(broken_pdf_upload())
        shown = self.stages_of(self.read())
        self.assertEqual([name for name, _, _ in shown], ["Reading pages"])
        self.assertEqual(shown[0][1], "failed")

    def test_the_stages_are_named_for_each_file_of_a_multi_file_upload(self) -> None:
        self.upload(*pdf_uploads(LINEAR_ALGEBRA_PDF, CELL_BIOLOGY_PDF))
        self.assertEqual(self.stage_names(self.read()).count("Reading pages"), 2)


class FailureIsolationTests(IngestTestCase):
    def three_files_one_broken(self) -> str:
        self.upload(
            pdf_upload(LINEAR_ALGEBRA_PDF),
            broken_pdf_upload(),
            pdf_upload(HANDOUT_NOTES_PDF),
        )
        return self.read()

    def test_one_unreadable_file_fails_without_taking_its_siblings_with_it(self) -> None:
        html = self.three_files_one_broken()
        self.assertIn("Eigenvalues", html)
        self.assertIn("sodium borohydride", html)

    def test_the_failed_file_is_still_listed_with_its_own_name(self) -> None:
        html = self.three_files_one_broken()
        self.assertIn("corrupt-deck.pdf could not be converted", html)

    def test_the_failed_file_says_why_and_what_to_do_about_it(self) -> None:
        html = self.three_files_one_broken()
        self.assertIn("could not be opened as a PDF", html)
        self.assertIn("splitting the file", html)

    def test_a_file_that_is_not_a_pdf_fails_on_its_own(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF), not_a_pdf_upload())
        html = self.read()
        self.assertIn("Eigenvalues", html)
        self.assertIn("notes.txt could not be converted", html)
        self.assertIn("not a PDF", html)

    def test_uploading_only_a_file_that_is_not_a_pdf_asks_for_a_pdf_again(self) -> None:
        response = self.upload(not_a_pdf_upload())
        self.assertEqual(response.status_code, 200)
        self.assertIn("not a PDF", response.content.decode())
        self.assertIn("Nothing uploaded yet", self.client.get("/").content.decode())

    def test_uploading_nothing_asks_for_a_file_again(self) -> None:
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Choose a PDF", response.content.decode())


@override_settings(INGEST_FILE_TIMEOUT_SECONDS=0)
class TimeoutTests(IngestTestCase):
    def test_a_file_that_outruns_the_time_limit_stops_and_explains_itself(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF))
        html = self.read()
        self.assertIn("math-201-eigenvalues.pdf could not be converted", html)
        self.assertIn("time limit", html)

    def test_the_explanation_says_what_to_do_instead(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF))
        self.assertIn("splitting the file", self.read())

    def test_a_stopped_file_still_reports_the_pages_it_did_read(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF))
        self.assertEqual(
            self.stages_of(self.read()),
            [
                ("Reading pages", "done", "read 5 pages"),
                (
                    "Converting",
                    "failed",
                    "We stopped converting it because it passed the time limit.",
                ),
            ],
        )

    def test_the_limit_is_counted_for_each_file_not_for_the_whole_upload(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF), pdf_upload(CELL_BIOLOGY_PDF))
        html = self.read()
        self.assertIn("math-201-eigenvalues.pdf could not be converted", html)
        self.assertIn("bio-110-membrane-transport.pdf could not be converted", html)
        self.assertEqual(html.count("could not be converted"), 2)
        stopped = [
            detail
            for _, outcome, detail in self.stages_of(html)
            if outcome == "failed"
        ]
        self.assertEqual(len(stopped), 2)
        self.assertTrue(all("time limit" in detail for detail in stopped))

    def test_nothing_is_stored_for_a_file_that_stopped(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF))
        self.assertNotIn("<section", self.read())


class ExtractedOnceTests(IngestTestCase):
    def test_reuploading_the_same_bytes_tells_the_student_it_was_not_converted_again(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF), title="First")
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF), title="Second")
        self.assertIn("reused the conversion", self.read(2))

    def test_the_second_course_reads_identically_to_the_first(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF), title="First")
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF), title="Second")

        def material(course_pk: int) -> str:
            html = self.read(course_pk)
            rendered = "".join(html.split("<section ")[1:])
            # Anchors, images and the paragraph's own open event are all
            # addressed by their row, so they differ between two courses holding
            # the same bytes. The material is what has to match.
            return re.sub(r'(id|src|hx-post)="[^"]*"', r'\1="X"', rendered)

        self.assertEqual(material(1), material(2))

    def test_the_first_upload_is_the_one_that_was_converted(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF), title="First")
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF), title="Second")
        self.assertNotIn("reused the conversion", self.read(1))

    def test_the_same_file_twice_in_one_upload_still_reads(self) -> None:
        self.upload(pdf_upload(LINEAR_ALGEBRA_PDF), pdf_upload(LINEAR_ALGEBRA_PDF))
        html = self.read()
        self.assertEqual(html.count("<section"), 10)
        self.assertEqual(len(set(re.findall(r'<section id="([^"]*)"', html))), 10)