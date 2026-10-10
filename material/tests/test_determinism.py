"""Conversion is deterministic: no model is involved and none is faked."""

import re

from django.test import TestCase

from material.tests.helpers import pdf_upload, use_temporary_media_root


class DeterminismTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)

    def sections_of(self, course_pk: int) -> str:
        """The rendered material, with per-row addresses normalised.

        Anchors are built from the file's own row, so two courses holding the
        same bytes anchor their sections differently by design; the paragraph's
        own open event is addressed by its row for the same reason. What has to
        match is the material itself.
        """
        html = self.client.get(f"/courses/{course_pk}/read/").content.decode()
        rendered = "".join(html.split("<section ")[1:])
        return re.sub(r'(id|src|hx-post)="[^"]*"', r'\1="X"', rendered)

    def test_two_courses_uploaded_from_one_pdf_read_identically(self) -> None:
        self.client.post("/courses/new/", {"title": "First", "files": pdf_upload()})
        self.client.post("/courses/new/", {"title": "Second", "files": pdf_upload()})

        first = self.sections_of(1)
        second = self.sections_of(2)
        self.assertIn("Eigenvalues", first)
        self.assertEqual(first, second)