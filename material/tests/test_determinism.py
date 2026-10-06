"""Conversion is deterministic: no model is involved and none is faked."""

import re

from django.test import TestCase

from material.tests.helpers import pdf_upload, use_temporary_media_root


class DeterminismTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)

    def sections_of(self, course_pk: int) -> str:
        """The rendered material, with per-file image URLs normalised away."""
        html = self.client.get(f"/courses/{course_pk}/read/").content.decode()
        rendered = "".join(html.split('<section id="')[1:])
        return re.sub(r'src="[^"]*"', 'src="IMG"', rendered)

    def test_two_courses_uploaded_from_one_pdf_read_identically(self) -> None:
        self.client.post("/courses/new/", {"title": "First", "file": pdf_upload()})
        self.client.post("/courses/new/", {"title": "Second", "file": pdf_upload()})

        first = self.sections_of(1)
        second = self.sections_of(2)
        self.assertIn("Eigenvalues", first)
        self.assertEqual(first, second)