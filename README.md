# Mastory

Upload a course PDF, read it as Markdown with its images, search inside it, and
keep the original one click away.

See `specs/0001-v0-personalised-learning-path.md` for the product this serves.

## Stack

Django, HTMX, Alpine.js, Postgres. No frontend build step.

## Running locally

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Set `DATABASE_URL` to a Postgres connection string in any deployed environment:

```sh
export DATABASE_URL=postgres://user:password@host:5432/mastory
```

Without it, the local SQLite file is used.

## Checks

```sh
.venv/bin/python -m mypy config material   # typecheck
.venv/bin/python manage.py test            # the full suite
```

## How it fits together

```
material/
  models.py    Course -> SourceFile -> Slide -> Span, every row carrying user_id
  ingest.py    deterministic PDF -> Markdown conversion, images extracted inline
  services.py  what an upload does: hash, cache, convert, store, retain the original
  render.py    Markdown -> HTML, stable anchors, search matching and highlighting
  views.py     the student-facing HTTP surface
  users.py     the single hardcoded user; every query is scoped by user_id
```

There is one test seam: HTTP. `material/tests/` drives the real application
through the Django test client against a real database, and asserts only on what
the student sees.

## Uploads

A student can hand over several PDFs at once; they become one course. Each file
is attempted on its own, so one that cannot be read is shown as a failure with a
reason and a next step while its siblings convert normally. Every file goes
through three named stages — reading pages, converting, verifying — which are
both logged and shown back to the student. Extraction is cached by file content
hash, so the same bytes are only ever converted once.

Converting a file that will not finish stops at a page boundary once
`INGEST_FILE_TIMEOUT_SECONDS` (120 by default) has passed, and the student is
told the file was abandoned rather than left waiting on it. A PDF whose pages
carry no text at all — a pure image-only scan — is rejected with a reason and a
next step; a scanned page inside an otherwise digital PDF is read by OCR, which
needs Tesseract's data files installed on the machine.

Regenerate the committed course PDF fixtures with
`.venv/bin/python scripts/make_fixtures.py`.