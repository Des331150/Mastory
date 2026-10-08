"""The upload-to-read path, driven through the HTTP seam.

A student uploads a course PDF and reads the converted material. Nothing here
touches the models or the converter directly: every assertion is about what the
student sees in a response.
"""

import re
from typing import Any

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from material.tests.helpers import (
    CELL_BIOLOGY_PDF,
    LINEAR_ALGEBRA_PDF,
    pdf_upload,
    use_temporary_media_root,
)

User = get_user_model()


def stream_of(response: Any) -> bytes:
    """The bytes a streaming response sent, whatever the test client wrapped."""
    return b"".join(response.streaming_content)


class UploadedCourseTestCase(TestCase):
    """A test case with a scratch MEDIA_ROOT and one course already uploaded."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.client.post("/courses/new/", {"title": "", "files": pdf_upload()})


class UploadAndReadTests(UploadedCourseTestCase):
    def read_page(self, path: str = "/courses/1/read/") -> str:
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_uploading_a_pdf_returns_the_student_to_their_material(self) -> None:
        html = self.read_page()
        self.assertIn("Eigenvalues and eigenvectors", html)

    def test_the_material_is_marked_up_as_markdown_not_dumped_as_text(self) -> None:
        html = self.read_page()
        self.assertIn("<h1", html)
        self.assertIn("<p>", html)

    def test_every_page_of_the_pdf_becomes_a_section(self) -> None:
        html = self.read_page()
        self.assertEqual(html.count("<section"), 5)

    def test_a_second_course_keeps_its_own_material(self) -> None:
        self.client.post(
            "/courses/new/",
            {"title": "Cell Biology", "files": pdf_upload(CELL_BIOLOGY_PDF)},
        )
        listing = self.client.get("/").content.decode()
        self.assertIn("Math 201 eigenvalues", listing)
        self.assertIn("Cell Biology", listing)

    def test_upload_rejects_a_file_that_is_not_a_pdf(self) -> None:
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            "/courses/new/",
            {
                "title": "Notes",
                "files": SimpleUploadedFile(
                    "notes.txt", b"just some notes", content_type="text/plain"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("not a PDF", response.content.decode())
        self.assertEqual(
            self.client.get("/").content.decode().count("course-list"), 1
        )

    def test_upload_without_a_file_says_so(self) -> None:
        response = self.client.post("/courses/new/", {"title": "Notes", "files": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Choose a PDF", response.content.decode())

    def test_an_htmx_upload_is_answered_with_a_redirect_the_client_follows(self) -> None:
        response = self.client.post(
            "/courses/new/",
            {"title": "Physics", "files": pdf_upload()},
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["HX-Redirect"], "/courses/2/read/")


class ImageTests(UploadedCourseTestCase):
    def test_extracted_images_are_kept_inline_with_the_material(self) -> None:
        html = self.client.get("/courses/1/read/").content.decode()
        self.assertIn("<img", html)

    def test_images_of_both_courses_survive_a_second_upload_of_the_same_pdf(self) -> None:
        self.client.post("/courses/new/", {"title": "Same again", "files": pdf_upload()})
        for course_pk in (1, 2):
            with self.subTest(course=course_pk):
                html = self.client.get(f"/courses/{course_pk}/read/").content.decode()
                match = re.search(r'<img[^>]*src="([^"]+)"', html)
                assert match is not None, html[:500]
                self.assertEqual(self.client.get(match.group(1)).status_code, 200)

    def test_an_inline_image_is_served_over_http(self) -> None:
        html = self.client.get("/courses/1/read/").content.decode()
        match = re.search(r'<img[^>]*src="([^"]+)"', html)
        assert match is not None, html[:500]
        src = match.group(1)
        response = self.client.get(src)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(stream_of(response).startswith(b"\x89PNG"))


class ConversionFailureTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)

    def upload_broken_pdf(self) -> str:
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.post(
            "/courses/new/",
            {
                "title": "Broken",
                "files": SimpleUploadedFile(
                    "scan.pdf",
                    b"%PDF-1.7\nthis is not really a pdf",
                    content_type="application/pdf",
                ),
            },
        )
        return self.client.get("/courses/1/read/").content.decode()

    def test_the_student_is_told_why_a_file_failed_and_what_to_do(self) -> None:
        html = self.upload_broken_pdf()
        self.assertIn("could not be converted", html)
        self.assertIn("splitting the file", html)

    def test_a_failed_upload_does_not_stop_the_next_one(self) -> None:
        self.upload_broken_pdf()
        self.client.post("/courses/new/", {"title": "Real", "files": pdf_upload()})
        html = self.client.get("/courses/2/read/").content.decode()
        self.assertIn("Eigenvalues", html)


class SearchTests(UploadedCourseTestCase):
    def test_search_finds_a_passage_the_student_half_remembers(self) -> None:
        html = self.client.get("/courses/1/read/?q=eigenspace").content.decode()
        self.assertIn("null space", html)
        self.assertIn("<mark>eigenspace</mark>", html.lower())

    def test_search_reports_how_many_sections_matched(self) -> None:
        html = self.client.get("/courses/1/read/?q=gradient").content.decode()
        self.assertIn("sections match", html)

    def test_search_matches_case_insensitively(self) -> None:
        html = self.client.get("/courses/1/read/?q=EIGENVALUES").content.decode()
        self.assertIn("<mark>", html.lower())

    def test_a_search_with_no_match_says_so_rather_than_showing_everything(self) -> None:
        html = self.client.get("/courses/1/read/?q=photosynthesis").content.decode()
        self.assertIn("No sections match", html)

    def test_the_search_box_keeps_the_query(self) -> None:
        html = self.client.get("/courses/1/read/?q=eigenvalue").content.decode()
        self.assertIn('value="eigenvalue"', html)


class AnchorTests(UploadedCourseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.html = self.client.get("/courses/1/read/").content.decode()

    def anchors(self, html: str) -> list[str]:
        return [
            part.split('"', 1)[0]
            for part in html.split('<section id="')[1:]
        ]

    def test_every_section_has_an_anchor(self) -> None:
        anchors = self.anchors(self.html)
        self.assertEqual(len(anchors), 5)
        self.assertTrue(all(anchors))
        self.assertEqual(len(set(anchors)), 5)

    def test_an_anchor_is_stable_across_requests(self) -> None:
        again = self.client.get("/courses/1/read/").content.decode()
        self.assertEqual(self.anchors(self.html), self.anchors(again))

    def test_an_anchor_does_not_depend_on_the_search_the_student_is_running(self) -> None:
        searching = self.client.get("/courses/1/read/?q=eigenvalue").content.decode()
        self.assertEqual(
            set(self.anchors(self.html)) & set(self.anchors(searching)),
            set(self.anchors(self.html)),
        )

    def test_a_link_to_an_anchor_lands_on_the_section_that_carries_it(self) -> None:
        target = self.anchors(self.html)[3]
        html = self.client.get(f"/courses/1/read/#{target}").content.decode()
        self.assertIn(f'<section id="{target}"', html)

    def test_the_anchor_index_links_straight_to_each_section(self) -> None:
        self.assertIn("On this page", self.html)
        self.assertIn(f'href="#{self.anchors(self.html)[0]}"', self.html)


class OriginalFileTests(UploadedCourseTestCase):
    def test_the_original_is_one_click_from_the_material(self) -> None:
        html = self.client.get("/courses/1/read/").content.decode()
        self.assertIn('href="/courses/1/original/1/"', html)
        self.assertIn("original", html.lower())

    def test_clicking_through_returns_the_pdf_the_student_uploaded(self) -> None:
        response = self.client.get("/courses/1/original/1/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(
            stream_of(response), LINEAR_ALGEBRA_PDF.read_bytes()
        )

    def test_an_unknown_original_is_not_served(self) -> None:
        self.assertEqual(self.client.get("/courses/1/original/99/").status_code, 404)


class IdentityTests(UploadedCourseTestCase):
    def test_v0_has_exactly_one_user_and_no_sign_in(self) -> None:
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_no_login_or_logout_surface_exists(self) -> None:
        for path in ("/accounts/login/", "/logout/"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_every_table_in_the_schema_carries_a_user_id(self) -> None:
        tables = {
            table
            for table in connection.introspection.table_names()
            if not table.startswith(("auth_", "django_"))
        }
        self.assertTrue(tables, "expected the extraction layer to create tables")
        for table in sorted(tables):
            with self.subTest(table=table):
                columns = {
                    column.name
                    for column in connection.introspection.get_table_description(
                        connection.cursor(), table
                    )
                }
                self.assertIn("user_id", columns)

    def test_material_belonging_to_another_user_is_invisible(self) -> None:
        from material.models import Course

        self.client.post("/courses/new/", {"title": "Mine", "files": pdf_upload()})
        other = User.objects.create(username="someone-else")
        foreign = Course.objects.create(user=other, title="Not yours")

        self.assertNotIn("Not yours", self.client.get("/").content.decode())
        self.assertEqual(
            self.client.get(f"/courses/{foreign.pk}/read/").status_code, 404
        )