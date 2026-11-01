"""The signed-in student: who every page is for, bound once per request.

There is no default account to fall back on. Django's authentication reads the
signed-in student out of the session, and the middleware below binds their id
once per request and unbinds it once it ends, so domain code that has no
request in hand still scopes itself to the one student the request belongs to
- and no page can run without one behind it.

An unbound identity raises rather than answering with a constant. Sharing one
default account between everyone is the fault that made this module necessary;
an id that silently handed itself out would be that fault under a new name.
"""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse

User = get_user_model()

#: Where signing in and signing up live. Said here once: the middleware leaves
#: these two pages open to a visitor, and every other page answers to the
#: student the session names. ``config/settings.py`` copies the login path into
#: ``LOGIN_URL`` because settings cannot import an app module.
LOGIN_PATH = "/accounts/login/"
SIGNUP_PATH = "/accounts/signup/"

#: The pages that run without a signed-in student, because they are how a
#: browser becomes one.
OPEN_PATHS = frozenset({LOGIN_PATH, SIGNUP_PATH})

_student_id: ContextVar[int | None] = ContextVar("mastory_student_id", default=None)


class NoSignedInStudent(RuntimeError):
    """Raised where a student id was asked for and no request bound one."""


def current_user_id() -> int:
    """The student this request belongs to."""
    if (identifier := _student_id.get()) is None:
        raise NoSignedInStudent(
            "no signed-in student here: every page runs through the identity "
            "middleware, so code reached this point by some way other than a "
            "request, and had no business asking"
        )
    return identifier


class SignedInMiddleware:
    """One place decides who a request belongs to, and nobody second-guesses it.

    Django's ``AuthenticationMiddleware`` has read the session by the time this
    runs, so ``request.user`` is either a student or nobody:

    - **A request with a student behind it is bound to them for its whole
      life.** Bound before the view and unbound after, whatever the view did,
      because an identity that outlives its request would leak one student's
      work into the next request on the same thread.
    - **A request with nobody behind it is sent to sign in**, carrying where it
      was going so the answer can hand it back. The two pages of signing in
      itself are left open, because there is nobody to be yet; everything else
      waits, including their posts.
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response: Callable[[HttpRequest], HttpResponse] = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        student: Any = getattr(request, "user", None)
        if student is None or not student.is_authenticated:
            if request.path in OPEN_PATHS:
                return self.get_response(request)
            return redirect_to_login(request.get_full_path())
        token = _student_id.set(int(student.pk))
        try:
            response = self.get_response(request)
        finally:
            _student_id.reset(token)
        return response


def owned(queryset: QuerySet[Any]) -> QuerySet[Any]:
    """Scope any queryset to the signed-in student."""
    return queryset.filter(user_id=current_user_id())
