"""The single hardcoded user of v0.

There is no authentication system in v0. The identity is fixed by
``settings.HARDCODED_USER_ID`` and created by a data migration, so every query
can already be written as if ``user_id`` existed.
"""

from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.http import HttpRequest

User = get_user_model()


def current_user_id() -> int:
    return settings.HARDCODED_USER_ID


def hardcoded_user() -> Any:
    return User.objects.get(pk=current_user_id())


class HardcodedUserMiddleware:
    """Attach the hardcoded user to every request.

    Queries stay scoped by ``user_id`` regardless, so replacing this middleware
    with real authentication in v1 does not touch domain logic.
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> Any:
        request.user = hardcoded_user()
        return self.get_response(request)


def owned(queryset: QuerySet[Any]) -> QuerySet[Any]:
    """Scope any queryset to the hardcoded user."""
    return queryset.filter(user_id=current_user_id())