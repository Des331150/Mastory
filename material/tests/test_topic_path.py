"""The topic path and the student's corrections to it, over the HTTP seam.

A student uploads material, sees the topics it covers, corrects what the model
got wrong, and confirms the path. The model is faked at ``material.model.complete``
and nothing above that is: every assertion here is about what the student sees.

The one thing worth reading closely is the grounding. A reply that cites a slide
the material does not have is not silently corrected into a real one, and a
reply the model is unsure of produces a flagged topic rather than a confident
wrong one, because those two are what make a topic path trustworthy.
"""

import json
import re
from typing import Any

from django.test import TestCase

from material import model
from material.model import NOT_CONFIGURED
from material.tests.helpers import (
    HANDOUT_NOTES_PDF,
    LINEAR_ALGEBRA_PDF,
    fake_topic,
    pdf_upload,
    use_fake_model,
    use_temporary_media_root,
)


class TopicPathTestCase(TestCase):
    """A scratch MEDIA_ROOT, one course uploaded, and a model under test's control."""

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)
        self.upload(LINEAR_ALGEBRA_PDF)

    def upload(self, *paths: Any, title: str = "") -> Any:
        return self.client.post(
            "/courses/new/",
            {"title": title, "files": [pdf_upload(path) for path in paths]},
        )

    def infer(self, *topics: Any, reply: str | None = None) -> str:
        """Work the topic path out, the way the student asks for it.

        Returns what the student is left looking at: the topic page after a
        redirect when a path was produced, and the refusal itself when the
        inference could not be grounded.
        """
        with use_fake_model(*topics, reply=reply):
            response = self.client.post("/courses/1/topics/infer/")
        if response.status_code == 200:
            return response.content.decode()
        return self.topics_page()

    def topics_page(self, course_pk: int = 1) -> str:
        response = self.client.get(f"/courses/{course_pk}/topics/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def titles(self, html: str) -> list[str]:
        """The topic titles, in the order the student is shown them."""
        found = re.findall(r'<h2>\s*\d+\.\s*(.*?)\s*</h2>', html)
        return [title.strip() for title in found]

    def pointed_slides(self, html: str) -> list[str]:
        """Every slide the path points at, as the student reads the list."""
        return [f"p{number}" for number in re.findall(r">p(\d+):", html)]

    def pk_of(self, html: str, title: str) -> str:
        """The id the page carries for a topic, found the way a student reads it.

        Inferred paths are replaced wholesale, so a row's id is not stable
        across requests and a test must not assume one.
        """
        for block in html.split('<li class="topic')[1:]:
            if f". {title}</h2>" in block:
                return re.search(r'id="topic-(\d+)"', block).group(1)  # type: ignore[union-attr]
        raise AssertionError(f"no topic titled {title!r} on the page")

    def confirm(self) -> str:
        response = self.client.post("/courses/1/topics/confirm/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class SeeingTheTopicPathTests(TopicPathTestCase):
    def test_the_student_sees_the_topics_their_material_covers(self) -> None:
        html = self.infer(fake_topic("Eigenvalues", [2, 3]), fake_topic("Eigenspaces", [4, 5]))
        self.assertEqual(self.titles(html), ["Eigenvalues", "Eigenspaces"])

    def test_topics_appear_in_course_order_not_in_the_order_the_model_answered(self) -> None:
        html = self.infer(fake_topic("Eigenspaces", [4, 5]), fake_topic("Eigenvalues", [2, 3]))
        self.assertEqual(self.titles(html), ["Eigenvalues", "Eigenspaces"])

    def test_the_page_starts_with_no_topics_rather_than_an_empty_schedule(self) -> None:
        html = self.topics_page()
        self.assertIn("No topics yet", html)

    def test_inferring_replaces_the_previous_path_rather_than_adding_to_it(self) -> None:
        self.infer(fake_topic("Eigenvalues", [2, 3]))
        html = self.infer(fake_topic("Diagonalisation", [5]))
        self.assertEqual(self.titles(html), ["Diagonalisation"])

    def test_a_topic_shows_the_slides_it_points_at(self) -> None:
        html = self.infer(fake_topic("Eigenspaces", [4, 5]))
        self.assertIn("Eigenvectors and eigenspaces", html)
        self.assertIn("p4", html)

    def test_a_topic_can_point_at_slides_that_are_not_next_to_each_other(self) -> None:
        html = self.infer(fake_topic("Diagonalisation", [2, 5]))
        self.assertEqual(self.pointed_slides(html), ["p2", "p5"])
        self.assertIn("from 2 different places", html)

    def test_a_slide_may_belong_to_two_topics(self) -> None:
        html = self.infer(fake_topic("Eigenvalues", [2, 3]), fake_topic("Repeated idea", [3, 4]))
        self.assertEqual(self.titles(html), ["Eigenvalues", "Repeated idea"])
        self.assertEqual(html.count("p3:"), 2)

    def test_headingless_material_still_gets_a_topic_path(self) -> None:
        self.upload(HANDOUT_NOTES_PDF, title="Organic chemistry")
        html = self.infer(fake_topic("Curved-arrow mechanisms", [1, 2]))
        self.assertEqual(self.titles(html), ["Curved-arrow mechanisms"])
        self.assertIn("p1", html)


class UncertainInferenceTests(TopicPathTestCase):
    def test_an_unsure_topic_is_flagged_rather_than_shown_as_settled(self) -> None:
        html = self.infer(fake_topic("Vaguely vector spaces", [2], confidence=0.2))
        self.assertIn("was not sure of this topic", html)
        self.assertIn("Check it against your slides", html)

    def test_a_confident_topic_is_not_flagged(self) -> None:
        html = self.infer(fake_topic("Eigenvalues", [2, 3], confidence=0.95))
        self.assertNotIn("was not sure of this topic", html)

    def test_a_weak_inference_is_flagged_even_when_the_material_is_thin(self) -> None:
        html = self.infer(fake_topic("Something about p2", [2], confidence=0.1))
        self.assertIn("Something about p2", html)
        self.assertIn("was not sure of this topic", html)

    def test_the_student_sees_a_flag_only_on_the_topic_that_earned_it(self) -> None:
        html = self.infer(
            fake_topic("Eigenvalues", [2], confidence=0.9),
            fake_topic("Doubtful topic", [4], confidence=0.1),
        )
        flagged = re.findall(r'class="topic flagged"', html)
        self.assertEqual(len(flagged), 1)
        self.assertIn("Doubtful topic", html)

    def test_renaming_a_flagged_topic_clears_its_flag(self) -> None:
        html = self.infer(fake_topic("Doubtful topic", [2], confidence=0.1))
        pk = self.pk_of(html, "Doubtful topic")
        self.client.post(f"/courses/1/topics/{pk}/rename/", {"title": "What I meant"})
        html = self.topics_page()
        self.assertNotIn("was not sure of this topic", html)
        self.assertIn("What I meant", html)


class GroundingTests(TopicPathTestCase):
    """A reply is only as good as its citations, and only if they exist."""

    def test_a_topic_citing_a_slide_that_does_not_exist_is_not_shown(self) -> None:
        html = self.infer(fake_topic("Invented topic", [99]))
        self.assertNotIn("Invented topic", html)
        self.assertIn("No topics could be worked out", html)

    def test_a_citation_the_material_lacks_is_dropped_and_the_rest_is_kept(self) -> None:
        html = self.infer(fake_topic("Half real", [2, 99]))
        self.assertEqual(self.pointed_slides(html), ["p2"])

    def test_a_topic_with_no_title_is_not_shown(self) -> None:
        html = self.infer({"title": "  ", "slides": [2], "confidence": 0.9})
        self.assertNotIn("slide-pointer-list", html)

    def test_a_reply_that_is_not_json_leaves_the_student_to_add_topics(self) -> None:
        html = self.infer(reply="I am afraid I cannot help with that.")
        self.assertIn("No topics could be worked out", html)
        self.assertIn("Add a topic", html)

    def test_a_reply_naming_no_topics_is_handled_the_same_way(self) -> None:
        html = self.infer(reply=json.dumps({"note": "nothing here"}))
        self.assertIn("No topics could be worked out", html)

    def test_the_existing_path_survives_a_reply_that_grounds_nothing(self) -> None:
        self.infer(fake_topic("Eigenvalues", [2, 3]))
        html = self.infer(reply="not json at all")
        self.assertIn("No topics could be worked out", html)
        self.assertEqual(self.titles(html), ["Eigenvalues"])

    def test_a_course_whose_files_all_failed_still_offers_a_topic_path(self) -> None:
        from material.tests.helpers import broken_pdf_upload

        self.client.post(
            "/courses/new/",
            {"title": "Broken", "files": broken_pdf_upload()},
        )
        html = self.client.get("/courses/2/topics/").content.decode()
        self.assertIn("No topics yet", html)


class WeightingTests(TopicPathTestCase):
    def test_a_topic_with_more_slides_is_worth_more_of_the_week(self) -> None:
        html = self.infer(fake_topic("Small", [2]), fake_topic("Large", [2, 3, 4, 5]))
        weights = [int(w) for w in re.findall(r"(\d+) units?", html)]
        self.assertTrue(weights)
        self.assertGreater(weights[-1], weights[0])

    def test_the_student_can_set_how_long_a_topic_takes(self) -> None:
        html = self.infer(fake_topic("Eigenvalues", [2, 3]))
        pk = self.pk_of(html, "Eigenvalues")
        self.client.post(f"/courses/1/topics/{pk}/weight/", {"weight": "42"})
        self.assertIn("42 units", self.topics_page())

    def test_a_rejected_weight_says_so_rather_than_crashing(self) -> None:
        html = self.infer(fake_topic("Eigenvalues", [2, 3]))
        pk = self.pk_of(html, "Eigenvalues")
        response = self.client.post(f"/courses/1/topics/{pk}/weight/", {"weight": "lots"})
        self.assertIn("whole number of units", response.content.decode())


class StudentEditTests(TopicPathTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.infer(
            fake_topic("Eigenvalues", [2, 3]),
            fake_topic("Eigenspaces", [4, 5]),
            fake_topic("Diagonalisation", [5]),
        )

    def edit(self, name: str, action: str, **fields: Any) -> Any:
        """POST one of a topic's edit forms, the way the page offers it."""
        pk = self.pk_of(self.topics_page(), name)
        return self.client.post(f"/courses/1/topics/{pk}/{action}/", fields)

    def test_a_renamed_topic_says_what_the_student_called_it(self) -> None:
        self.edit("Eigenvalues", "rename", title="Characteristic polynomials")
        self.assertIn("Characteristic polynomials", self.topics_page())

    def test_a_rename_persists_across_requests(self) -> None:
        self.edit("Eigenspaces", "rename", title="Null spaces")
        self.assertEqual(self.titles(self.topics_page())[1], "Null spaces")

    def test_a_removed_topic_is_gone_and_the_rest_keep_their_order(self) -> None:
        self.edit("Eigenspaces", "remove")
        self.assertEqual(self.titles(self.topics_page()), ["Eigenvalues", "Diagonalisation"])

    def test_a_moved_topic_takes_its_new_place_in_the_order(self) -> None:
        self.edit("Diagonalisation", "move", direction="up")
        self.assertEqual(
            self.titles(self.topics_page()),
            ["Eigenvalues", "Diagonalisation", "Eigenspaces"],
        )

    def test_moving_the_first_topic_up_leaves_it_where_it_is(self) -> None:
        self.edit("Eigenvalues", "move", direction="up")
        self.assertEqual(self.titles(self.topics_page())[0], "Eigenvalues")

    def test_two_topics_that_are_really_one_become_one(self) -> None:
        into = self.pk_of(self.topics_page(), "Eigenspaces")
        self.edit("Eigenvalues", "merge", into=into)
        html = self.topics_page()
        self.assertEqual(self.titles(html), ["Eigenvalues", "Diagonalisation"])
        self.assertEqual(self.slides_of(html, "Eigenvalues"), ["p2", "p3", "p4", "p5"])

    def test_a_merged_topic_keeps_both_slides_of_what_used_to_be_the_boundary(self) -> None:
        into = self.pk_of(self.topics_page(), "Diagonalisation")
        self.edit("Eigenvalues", "merge", into=into)
        self.assertEqual(
            self.slides_of(self.topics_page(), "Eigenvalues"), ["p2", "p3", "p5"]
        )

    def slides_of(self, html: str, title: str) -> list[str]:
        """The slides one topic on the page points at."""
        for block in html.split('<li class="topic')[1:]:
            if f". {title}</h2>" in block:
                return [
                    f"p{number}" for number in re.findall(r">p(\d+):", block)
                ]
        raise AssertionError(f"no topic titled {title!r} on the page")

    def test_a_topic_the_student_adds_appears_in_the_path(self) -> None:
        self.client.post("/courses/1/topics/add/", {"title": "Titration curves"})
        self.assertEqual(self.titles(self.topics_page())[-1], "Titration curves")

    def test_an_added_topic_needs_a_name_the_student_can_recognise(self) -> None:
        response = self.client.post("/courses/1/topics/add/", {"title": "   "})
        self.assertIn("Give the topic a name", response.content.decode())

    def test_a_topic_covering_three_ideas_becomes_two_study_sessions(self) -> None:
        self.infer(fake_topic("Whole chapter", [2, 3, 4, 5]))
        self.edit("Whole chapter", "split", slide="3", title="Eigenspaces")
        html = self.topics_page()
        self.assertEqual(self.titles(html), ["Whole chapter", "Eigenspaces"])
        self.assertEqual(self.pointed_slides(html), ["p2", "p3", "p4", "p5"])

    def test_splitting_after_the_last_slide_says_there_is_nothing_after_it(self) -> None:
        self.infer(fake_topic("Whole chapter", [2, 3, 4]))
        response = self.edit("Whole chapter", "split", slide="4")
        self.assertIn("nothing after it", response.content.decode())

    def test_a_split_gives_the_second_topic_its_own_name_when_asked(self) -> None:
        self.infer(fake_topic("Whole chapter", [2, 3, 4]))
        self.edit("Whole chapter", "split", slide="2", title="Characteristic polynomials")
        self.assertEqual(
            self.titles(self.topics_page()),
            ["Whole chapter", "Characteristic polynomials"],
        )

    def test_an_edit_that_names_no_slide_to_split_after_says_so(self) -> None:
        self.infer(fake_topic("Whole chapter", [2, 3, 4]))
        response = self.edit("Whole chapter", "split")
        self.assertEqual(response.status_code, 404)


class ConfirmationTests(TopicPathTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.infer(fake_topic("Eigenvalues", [2, 3]), fake_topic("Eigenspaces", [4, 5]))

    def test_the_path_asks_to_be_confirmed_before_a_schedule_exists(self) -> None:
        html = self.topics_page()
        self.assertIn("Confirm this topic path", html)
        self.assertIn("A schedule is built on these topics", html)

    def test_confirming_is_said_back_to_the_student(self) -> None:
        self.assertIn("Topic path confirmed", self.confirm())

    def test_a_confirmed_path_says_it_can_be_planned_on(self) -> None:
        self.assertIn("a schedule can be built on it", self.confirm())

    def test_there_is_nothing_to_confirm_before_any_topic_exists(self) -> None:
        for name in self.titles(self.topics_page()):
            pk = self.pk_of(self.topics_page(), name)
            self.client.post(f"/courses/1/topics/{pk}/remove/")
        html = self.topics_page()
        self.assertNotIn("Confirm the topic path", html)
        response = self.client.post("/courses/1/topics/confirm/")
        self.assertIn("no topics to confirm", response.content.decode())

    def test_editing_after_confirming_puts_the_path_back_in_front_of_the_student(self) -> None:
        self.confirm()
        pk = self.pk_of(self.topics_page(), "Eigenvalues")
        self.client.post(f"/courses/1/topics/{pk}/rename/", {"title": "Something else"})
        html = self.topics_page()
        self.assertIn("Confirm this topic path", html)
        self.assertNotIn("a schedule can be built on it", html)

    def test_confirming_then_adding_a_topic_requires_confirming_again(self) -> None:
        self.confirm()
        self.client.post("/courses/1/topics/add/", {"title": "Late addition"})
        self.assertIn("Confirm this topic path", self.topics_page())

    def test_looking_at_the_path_does_not_confirm_it(self) -> None:
        self.topics_page()
        self.assertIn("Confirm this topic path", self.topics_page())


class ModelBoundaryTests(TopicPathTestCase):
    def test_the_model_is_only_ever_reached_through_one_function(self) -> None:
        self.assertTrue(callable(model.complete))
        self.assertTrue(callable(model.infer_topics))
        with use_fake_model(fake_topic("Eigenvalues", [2])):
            self.client.post("/courses/1/topics/infer/")
        self.assertIn("Eigenvalues", self.topics_page())

    def test_a_model_that_is_not_configured_says_so_rather_than_failing(self) -> None:
        from unittest import mock

        from material.model import ModelUnavailable

        with mock.patch.object(
            model, "complete", side_effect=ModelUnavailable(*NOT_CONFIGURED)
        ):
            response = self.client.post("/courses/1/topics/infer/")
        html = response.content.decode()
        self.assertIn("no language model configured", html)
        self.assertIn("MODEL_BASE_URL", html)

    def test_the_student_can_still_add_topics_without_a_model(self) -> None:
        from unittest import mock

        from material.model import ModelUnavailable

        with mock.patch.object(
            model,
            "complete",
            side_effect=ModelUnavailable("Unreachable.", "Try again."),
        ):
            self.client.post("/courses/1/topics/infer/")
        self.client.post("/courses/1/topics/add/", {"title": "Added by hand"})
        self.assertIn("Added by hand", self.topics_page())


class NavigationTests(TopicPathTestCase):
    def test_the_topic_path_is_reachable_from_the_material(self) -> None:
        html = self.client.get("/courses/1/read/").content.decode()
        self.assertIn('href="/courses/1/topics/"', html)

    def test_a_topic_of_another_course_is_not_reachable(self) -> None:
        self.upload(HANDOUT_NOTES_PDF, title="Organic chemistry")
        self.assertEqual(
            self.client.get("/courses/2/topics/1/rename/").status_code, 405
        )

    def test_editing_a_topic_that_does_not_exist_is_not_served(self) -> None:
        self.infer(fake_topic("Eigenvalues", [2]))
        self.assertEqual(
            self.client.get("/courses/1/topics/99/remove/").status_code, 405
        )
        self.assertEqual(
            self.client.post("/courses/1/topics/99/remove/").status_code, 404
        )