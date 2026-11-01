"""The pages where a browser becomes a student, and a student becomes nobody.

Three pages, and Django's own machinery under each: ``authenticate`` checks the
password, ``login`` opens the session, ``logout`` closes it, and the validators
the project names in its settings judge a password. What is decided here is
only what the student is told when the machinery says no.

Two decisions earn their place:

- **A wrong username and a wrong password read the same.** One sentence covers
  both halves on purpose: telling a stranger which half was wrong tells them
  which half to keep working on.
- **A refusal never leaves the page.** Every failure is the same page again
  with the reason on it and the username the student chose still filled in -
  except the passwords, which arrived once and are not filled back in.
"""

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from material.users import LOGIN_PATH

User = get_user_model()

#: Where a signed-in student goes. Every landing in this module is the course
#: list, the page the whole app starts from.
_HOME = "/"

_WRONG_CREDENTIALS = "No student with that username and password."


def _landing(request: HttpRequest) -> str:
    """Where a sign-in was heading, when the ``next`` it carried was safe to
    honour. A ``next`` pointed at another site would turn a sign-in page into a
    lever that sends a student wherever the link wanted; the same site's own
    paths are the only ones that may be honoured."""
    wanted = request.POST.get("next") or request.GET.get("next") or ""
    if wanted and url_has_allowed_host_and_scheme(
        wanted, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return wanted
    return _HOME


@require_POST
def sign_out(request: HttpRequest) -> HttpResponse:
    """Leave.

    A ``GET`` that could end a session would sign a student out from across the
    web by way of a link or a prefetch, so leaving is a press, like every other
    change this app takes.
    """
    logout(request)
    return redirect(LOGIN_PATH)


def sign_in(request: HttpRequest) -> HttpResponse:
    """Come back to an account that already exists."""
    username = request.POST.get("username", "")
    if request.method == "POST":
        student = authenticate(
            request,
            username=username.strip(),
            password=request.POST.get("password", ""),
        )
        if student is not None:
            login(request, student)
            return redirect(_landing(request))
        return _page(request, "login.html", "Sign in", _WRONG_CREDENTIALS, username)
    return _page(request, "login.html", "Sign in", "", username)


def sign_up(request: HttpRequest) -> HttpResponse:
    """Make an account, and be signed in by it."""
    if request.user.is_authenticated:
        # A student already holding a session pressing sign up would otherwise
        # overwrite it with a fresh account, and their material would slip out
        # of view. The session this page runs under is already somebody's.
        return redirect(_HOME)

    username = request.POST.get("username", "")
    password = request.POST.get("password", "")
    if request.method == "POST":
        chosen = username.strip()
        error = _refusal(chosen, password, request.POST.get("confirmation", ""))
        if not error:
            try:
                # In its own savepoint: a taken name raises under the
                # uniqueness the database itself holds, and the rollback stops
                # there rather than poisoning whatever the request is inside.
                with transaction.atomic():
                    student = User.objects.create_user(chosen, password=password)
            except IntegrityError:
                error = "That username is already taken."
            else:
                login(request, student)
                return redirect(_HOME)
        return _page(request, "signup.html", "Sign up", error, username)
    return _page(request, "signup.html", "Sign up", "", username)


def _refusal(username: str, password: str, confirmation: str) -> str:
    """Everything a signup must get right before an account is made, named once
    it misses. An empty string is the sound of a form that said nothing wrong.

    The username is part of who a student is here, because it is what signs
    them in. The passwords are judged by the project's own validators, so the
    bar an account must clear is one bar wherever a signup happens.
    """
    if not username:
        return "The username is what signs you in, so give the form something to read."
    if len(username) > User._meta.get_field("username").max_length:
        return "That username is too long."
    if password != confirmation:
        return "The two passwords are different."
    try:
        validate_password(password, user=User(username=username))
    except ValidationError as reasons:
        return " ".join(reasons.messages)
    return ""


def _page(
    request: HttpRequest, template: str, title: str, error: str, username: str
) -> HttpResponse:
    """A page of signing in or up, the way the student left it: with the reason
    if there is one, and the name they chose still filled in."""
    return render(
        request,
        f"material/{template}",
        {"title": title, "error": error, "username": username},
    )
