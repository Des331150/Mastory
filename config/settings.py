"""Django settings for the Mastory project.

One language, one deployable, no frontend build step: Django, HTMX, Alpine.js
and Postgres. See ``specs/0001-v0-personalised-learning-path.md``.
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-local-development-key-only",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "material",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "material.users.HardcodedUserMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Postgres in every deployed environment, named by DATABASE_URL. SQLite is the
# local fallback only, for running the suite without a Postgres server to hand.
DATABASE_URL = os.environ.get("DATABASE_URL")

DATABASES = {
    "default": dj_database_url.config(
        default=DATABASE_URL or f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    )
}

if not DATABASE_URL:  # pragma: no cover - operator warning
    import warnings

    warnings.warn(
        "DATABASE_URL is unset: falling back to local SQLite. "
        "Mastory is built for Postgres.",
        RuntimeWarning,
        stacklevel=2,
    )

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Accra"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Media holds the retained originals and the images extracted from them.
MEDIA_ROOT = Path(os.environ.get("MASTORY_MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_URL = "media/"

# v0 has one hardcoded user and no authentication system. Every query is still
# written as if ``user_id`` existed, so v1 adds auth without touching domain
# logic.
HARDCODED_USER_ID = int(os.environ.get("MASTORY_USER_ID", "1"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}