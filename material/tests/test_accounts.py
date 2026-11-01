"""The pages of signing in, and the claim they exist to make true.

Until now the app served exactly one student, fixed by a setting, so a second
student in the same deployment *was* the first one: they saw and modified the
same courses, quizzes and mastery. These tests drive the pages that replace
that constant with a signed-in person: arrive with no session and be asked who
you are, make an account, come back to it, and - the point of the whole change -
two students on the same deployment still see each other's material as nothing
at all.
"""

from typing import Any

from django.contrib.auth import get_user_model
from django.test import TestCase

from material import users
from material.tests.helpers import (
    CELL_BIOLOGY_PDF,
    LINEAR_ALGEBRA_PDF,
    TEST_PASSWORD,
    flat,
    pdf_upload,
    use_temporary_media_root,
)

User = get_user_model()


def sign_up(
    client: Any,
    username: str,
    password: str = TEST_PASSWORD,
    confirmation: str | None = None,
) -> Any:
    """Press the signup form, and land wherever the app sends a new student."""
    return client.post(
        "/accounts/signup/",
        {
            "username": username,
            "password": password,
            "confirmation": password if confirmation is None else confirmation,
        },
        follow=True,
    )


def log_in(client: Any, username: str, password: str = TEST_PASSWORD, **extra: Any) -> Any:
    """Sign in through the page, as the form posts it."""
    return client.post(
        "/accounts/login/", {"username": username, "password": password, **extra},
        follow=True,
    )


def log_out(client: Any) -> Any:
    """Press the logout button, as the header posts it."""
    return client.post("/accounts/logout/", follow=True)


class GuestTests(TestCase):
    """A browser that has never signed in: no identity, and so nothing to show."""

    def test_signing_up_makes_a_student_and_signs_them_in(self) -> None:
        """One form fills from the student of nobody; one course list of them."""
        response = sign_up(self.client, "ada")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(username="ada").exists())
        self.assertEqual(
            response.context["user"].username,
            "ada",
            "the page a new student landed on was not served as them",
        )

    def test_the_pages_of_signing_in_themselves_open_to_a_guest(self) -> None:
        for path in ("/accounts/login/", "/accounts/signup/"):
            response = self.client.get(path)
            with self.subTest(path=path):
                self.assertEqual(response.status_code, 200)

    def test_every_other_page_first_asks_who_the_student_is(self) -> None:
        """An anonymous visit is sent to sign in, carrying where it was going,
        so the pages it wanted can be handed back after the answer."""
        for path in (
            "/",
            "/courses/new/",
            "/courses/1/read/",
            "/courses/1/topics/",
            "/courses/1/plan/",
            "/courses/1/exam/",
        ):
            response = self.client.get(path)
            with self.subTest(path=path):
                self.assertEqual(response.status_code, 302)
                self.assertTrue(
                    response["Location"].startswith("/accounts/login/?next="),
                    f"an anonymous visit to {path} was not sent to sign in",
                )

    def test_an_identity_not_bound_to_a_page_cannot_be_borrowed(self) -> None:
        """The fault this change exists to kill was quiet: a constant answered
        to whoever asked, and every student in a deployment shared it. Nothing
        still answers like that; a page with no student behind it refuses to
        name one rather than naming somebody else's."""
        with self.assertRaises(users.NoSignedInStudent):
            users.current_user_id()


class SignupTests(TestCase):
    """Making an account: the one door a deployment opens to a stranger."""

    def test_a_taken_username_is_refused_and_says_so(self) -> None:
        """The page comes back with the reason on it and the name the student
        chose still filled in, and no second row is made."""
        sign_up(self.client, "ada")
        log_out(self.client)
        users_before = User.objects.count()

        response = sign_up(self.client, "ada")

        self.assertEqual(response.status_code, 200)
        self.assertIn("already taken", flat(response.content.decode()))
        self.assertEqual(User.objects.count(), users_before, "a refused signup still made an account")

    def test_the_two_passwords_must_agree(self) -> None:
        """Typed twice so a slip of the hand cannot lock a student out of an
        account that was made the way they typed it, not the way they meant."""
        response = sign_up(self.client, "ada", confirmation="different entirely")

        self.assertIn("two passwords are different", flat(response.content.decode()))
        self.assertFalse(User.objects.filter(username="ada").exists())

    def test_a_password_the_checks_refuse_is_refused(self) -> None:
        response = sign_up(self.client, "ada", password="short")

        self.assertIn("too short", flat(response.content.decode()))
        self.assertFalse(User.objects.filter(username="ada").exists())

    def test_an_unnamed_student_is_refused(self) -> None:
        """The username is what signs a person in; the form will not take a
        blank one and leave an account nobody can reach."""
        response = sign_up(self.client, "")

        self.assertIn("username", flat(response.content.decode()))
        self.assertFalse(User.objects.filter(username="ada").exists())


class ReturningTests(TestCase):
    """An account that already exists, arriving to use it again."""

    def setUp(self) -> None:
        super().setUp()
        User.objects.create_user("ada", password=TEST_PASSWORD)

    def test_a_student_who_made_the_account_signs_in_with_it(self) -> None:
        response = log_in(self.client, "ada")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["user"].username, "ada")
        self.assertIn("Your courses", flat(response.content.decode()))

    def test_the_wrong_password_says_so_without_saying_which_half_was_wrong(self) -> None:
        """One sentence covers both halves on purpose: separating a wrong
        username from a wrong password tells a stranger which one to keep."""
        response = log_in(self.client, "ada", "everything-else-wrong-42")

        self.assertIn(
            "No student with that username and password",
            flat(response.content.decode()),
        )
        self.assertEqual(self.client.get("/").status_code, 302, "the refusal kept a session")

    def test_an_unknown_username_wears_the_same_words(self) -> None:
        response = log_in(self.client, "nobody-here", TEST_PASSWORD)

        self.assertIn(
            "No student with that username and password",
            flat(response.content.decode()),
        )

    def test_signing_in_can_carry_on_where_the_visit_was_going(self) -> None:
        response = self.client.get("/courses/new/")
        self.assertTrue(response["Location"].startswith("/accounts/login/?next="))

        landing = self.client.post(
            "/accounts/login/",
            {"username": "ada", "password": TEST_PASSWORD, "next": "/courses/new/"},
        )
        self.assertEqual(landing["Location"], "/courses/new/")

    def test_leaving_takes_a_post_and_ends_the_session(self) -> None:
        """A page that ended a session because somebody linked to it could sign
        a student out from across the web; leaving is a student's own press."""
        self.client.force_login(User.objects.get(username="ada"))
        self.assertEqual(self.client.get("/accounts/logout/").status_code, 405)

        log_out(self.client)
        self.assertEqual(self.client.get("/").status_code, 302, "leaving kept the session")


class SecondStudentTests(TestCase):
    """Two accounts on one deployment, and what each can see of the other's.

    This is the claim issue 22 says nothing else in the product could answer:
    a second student in the same deployment is not the first one. They drive
    the ordinary pages through their own session, because that is how a second
    student would really be here.
    """

    def setUp(self) -> None:
        super().setUp()
        use_temporary_media_root(self)

    def test_two_students_never_see_each_others_material(self) -> None:
        sign_up(self.client, "ada")
        self.client.post(
            "/courses/new/",
            {"title": "Calculus", "files": [pdf_upload(LINEAR_ALGEBRA_PDF)]},
        )
        log_out(self.client)

        sign_up(self.client, "grace")
        page = flat(self.client.get("/").content.decode())
        self.assertIn("Nothing uploaded yet", page)
        self.assertNotIn("Calculus", page)

        self.client.post(
            "/courses/new/",
            {"title": "Botany", "files": [pdf_upload(CELL_BIOLOGY_PDF)]},
        )
        page = flat(self.client.get("/").content.decode())
        self.assertIn("Botany", page)
        self.assertNotIn("Calculus", page)
        self.assertEqual(self.client.get("/courses/2/read/").status_code, 200)

        for path in (
            "/courses/1/read/",
            "/courses/1/topics/",
            "/courses/1/plan/",
            "/courses/1/exam/",
        ):
            response = self.client.get(path)
            with self.subTest(path=path):
                self.assertEqual(response.status_code, 404, f"grace saw {path}")

        log_out(self.client)
        log_in(self.client, "ada")
        page = flat(self.client.get("/").content.decode())
        self.assertIn("Calculus", page)
        self.assertNotIn("Botany", page)
        self.assertEqual(self.client.get("/courses/2/read/").status_code, 404)

    def test_a_students_course_alone_names_its_own_sections(self) -> None:
        """The scoping runs through the reading surface itself, not only the
        lists: the paragraphs one student reads are the ones they uploaded."""
        sign_up(self.client, "ada")
        self.client.post(
            "/courses/new/",
            {"title": "Calculus", "files": [pdf_upload(LINEAR_ALGEBRA_PDF)]},
        )
        page = flat(self.client.get("/courses/1/read/").content.decode())
        self.assertIn("Eigenvalues and eigenvectors", page)

        log_out(self.client)
        sign_up(self.client, "grace")
        response = self.client.get("/courses/1/read/")
        self.assertEqual(response.status_code, 404)
