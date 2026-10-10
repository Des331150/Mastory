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
  models.py    Course -> SourceFile -> Slide -> Span, and the plan Course -> Plan -> Session, and the student's record on Topic
  ingest.py    deterministic PDF -> Markdown conversion, images extracted inline
  model.py     the only place a model is called, and the grounding contract it enforces
  topics.py    inferring the topic path, and every edit the student makes to it
  planning.py  the week of sessions: weighting, the weekly budget, and the two modes
  progress.py  what the student has done: the share of their plan, the log, and quiz attempts
  quiz.py      a topic's quiz: written from spans, cited to them, marked against them, and retaken
  reading.py   whether the student has opened a cited paragraph
  services.py  what an upload does: hash, cache, convert, store, retain the original
  render.py    Markdown -> HTML, stable anchors, search matching and highlighting
  views.py     the student-facing HTTP surface
  users.py     the single hardcoded user; every query is scoped by user_id
```

There is one test seam: HTTP. `material/tests/` drives the real application
through the Django test client against a real database, and asserts only on what
the student sees. The one exception comes from work that has no HTTP surface
yet: the no-gamification claim reads the templates and the schema rather than a
page, because a word nobody renders is exactly the one worth forbidding.

## The model

Every model call in Mastory goes through `material/model.py`, and all of them
obey one grounding contract: a call is given the student's own material and
nothing else, and what comes back has to point at that material. The contract is
enforced in code rather than asked for in a prompt — a reply citing a slide the
material does not have loses that citation, a topic left pointing at nothing is
dropped rather than shown, and a topic the model is unsure about comes back
flagged so the student checks it. `model.complete()` is the only function that
leaves the machine, and it is where the test suite's deterministic fake is
installed; no HTTP test knows the fake exists.

Set `MASTORY_MODEL_BASE_URL`, `MASTORY_MODEL_API_KEY` and `MASTORY_MODEL_NAME`
for any OpenAI-compatible chat completions endpoint. Unset, the topic path says
so to the student and they can add topics by hand.

## Topics

A student sees the topics their material covers, in course order, and corrects
them before anything is built on top. Topics are inferred from the slides
rather than taken from the deck's outline, because plenty of material has no
structure to take them from; a topic points at slides through join rows, which is
what lets one topic cover slides that are not next to each other. Weight is
derived from that pointer map, not asked for, so the model cannot invent a
weighting.

The student can rename, split, merge, reorder, re-weight, add and remove, and
every edit persists. Any edit clears the confirmation, because a path edited
since is not the path the student said was right.

No schedule is generated until the student confirms. The gate is
`topics.require_confirmation()`, which raises rather than returning a boolean,
so the schedule ticket inherits the rule instead of deciding whether it needs
one; `/courses/<id>/schedule-check/` exposes it so the refusal is visible and
testable before a planner exists.

## The week

`/courses/<id>/plan/` is the schedule. It comes through the same confirmation
gate, so a week over topics the student has not checked cannot be built.

The student says when the exam is, how many days a week they can study and how
many hours, and the week is built inside that. There is no model call here:
the order is the order they confirmed, the weight is what the pointer map
already said, and the two numbers are theirs. Generation is arithmetic a
student could check by hand.

- **One topic is one session.** Never split across days, never two topics in a
  sitting. The shape is in the schema — a unique constraint per topic per plan —
  so it cannot be got wrong by a later edit.
- **Sessions are offered fewer, not more.** The count is the lesser of the days
  they study and the number their budget carries at a 30-minute floor. An hour
  a week is two sessions, not five that would each be twelve minutes.
- **Weight sets the length.** A topic covering more material gets more of the
  week. The week's total never exceeds the budget, and no topic is promised a
  sitting over three hours, because a topic cannot be split to make it fit.
  When that ceiling means some of the stated hours cannot be used, the week says
  so rather than totalling quietly short of the number the student typed.
- **Study days are spread across the working week.** Three days is Monday,
  Wednesday and Friday; five is the whole working week; Saturday and Sunday only
  appear for a student who asked for six or seven. The student picks a number of
  days rather than the days themselves, so the page names the ones it assumed.
- **Weeks are calendar weeks.** A plan built on a Thursday does not put Thursday
  and Monday in one panel and call it a week.

Two modes, and the exam date is the only difference. **Exam mode** counts down,
and nothing lands on or after the exam; topics that will not fit before it are
named rather than quietly dropped. **Open mode**, before there is a date, is the
topic path in the student's order with no countdown and no claim about when
they will finish — which is what makes the app worth opening in week three of
semester.

Rebuilding replaces every session, which is right while a schedule is a function
of the topics and the time available. What the student has recorded is not on
the session at all — it is on the topic, because a session is the plan's row and
the plan is replaced whenever the hours change. A topic is the thing the student
finished; the plan moves it around and the record stays put.

## Keeping track

The same page shows how much of the plan the student has done, and the log of
what they actually did. Deliberately not a streak: a streak breaks on one missed
day and punishes exactly the student the shift-never-compress model exists to
protect. There is no streak counter, no points and no leaderboard anywhere in
the product, and `material/tests/test_session_log.py` asserts that against every
template and against the schema.

- **A share of the sessions planned.** The denominator is what the student typed
  into the planner, not the sessions still ahead, because "done a third of my
  plan" has to mean the same thing on the first day as on the last.
- **One press, and it is a toggle.** Each session carries a button for done and
  one for skipped; pressing the button for the state it is already in puts it
  back. Done and skipped are mutually exclusive and set together, so the log can
  answer "what happened on the fourth" with one answer.
- **Skipping costs nothing.** It leaves the numerator where it was, so it takes
  nothing off what the student has done, and it leaves the denominator where it
  was too, so it cannot be used to flatter the share by pretending the session
  was never planned. It is named on the page as a choice, which is the
  difference between "not doing this" and "failing at this".
- **The log is what happened, not what is waiting.** Only sessions the student
  marked appear in it, in the order of the days they marked them. A session
  nobody has marked is simply not history yet, and a day nobody marked is not
  called a miss.

The share and the log are counted off the same read of the plan in
`planning.overview`, so the number and the ledger underneath it cannot tell
different stories.

### Falling behind

This is the mechanic that distinguishes a planner that works from one that
merely nags.

- **A missed session shifts later. It never compresses.** A rebuild lays the
  work still waiting onto the next study days from today. It does not refill
  the missed days, does not add a session to a day that already has one, and
  does not hand a week out twice because one of its sessions is done.
  Redistributing the same work into fewer days produces a plan the student
  already knows is impossible, which is how they abandon it.
- **Finished work gives nothing back.** A session the student completed keeps
  the day they recorded it and does not free a slot for anything else. A
  rebuild re-plans what is ahead, not what is behind.
- **When it will not fit, the page says the day it actually reaches.** The
  projection lifts the exam horizon off and counts only the outstanding work,
  at the pace the student said they can manage. It answers "what day do I stop
  on?", and it says how many days late that is — including when it lands on
  the exam itself, which is no use as a plan.
- **Dropping a topic is the student's call, and it is priced first.** Every
  topic with a session is offered with the length that session actually takes
  and the finish date cutting it buys, plus whether that date is any use on
  its own. A choice the student cannot price is a choice they are guessing at.
- **A cut is reversible, and it cannot reach the record.** One control puts a
  cut back, offered whether or not the student is still behind. A topic
  already done or skipped is refused: its session is the row the share, the
  log and the meter are counted from.

`Topic.cut_on` sits beside `completed_on` and `skipped_on` on the topic rather
than on the session, because a cut is a decision the plan rebuilds around and
must not lose.

### Attempts

`Attempt` records a sitting of a topic's quiz: when it was taken, the score as a
fraction, and how many times its citations verified. It is recorded from the
first quiz onwards because none of it can be backfilled — mastery will be weighed
out of these rows later, and a table invented after the fact starts empty and
stays wrong. `progress.record_attempt()` refuses a score outside zero to one
rather than storing it, so nothing downstream inherits a mark counted out of the
wrong number. Each row also keeps the paragraphs its wrong answers came from,
as `AttemptMissed` rows rather than ids in a column: they are what the retry
rule asks about, and a quiz written again leaves nothing behind to work them out
from. `material/quiz.py` calls it once a sitting has been marked, and
`/courses/<id>/topics/<id>/quiz/` is where a student reaches it.

## Quizzes

A topic quiz is written from the student's own paragraphs, one paragraph at a
time. Each question cites the paragraph it came from, is put back to the model
against that paragraph, and is dropped if the paragraph does not state the
answer — so a topic whose material supports one verified question gets one
question and a low-confidence flag rather than four invented ones. The count of
dropped questions is shown rather than hidden, because a quiz that silently got
smaller reads as a fault rather than as a decision.

Grading is multiple choice and short answer, and is a string comparison against
the answers stored with each question, which were themselves checked against the
cited paragraph. That is what makes grading unable to contradict its source: the
same normalisation checks an answer against the span and marks it against the
student. A wrong answer sends the student to the exact paragraph, via an anchor
the reading page renders per paragraph rather than per slide.

### Taking it again

Retaking a topic quiz is unlimited and free. Nothing is spent on a sitting and
no attempt is taken away, so getting one wrong ends nothing: the next press
writes the quiz again, from the same paragraphs, and the questions are new.

- **Retakes regenerate.** Every one of them writes the quiz again, which is
  what stops a retake being a way of memorising an answer.
- **After two failed sittings the next attempt waits.** A sitting is a failure
  below half right — the line is `models.PASS_MARK`, kept beside the table so
  the word cannot mean two things — and the rule is read off `Attempt.failed`,
  so the page counting attempts and the row that recorded one cannot disagree.
- **It waits for the material, not for a decision.** The refused attempt is a
  server answer, not a panel a student can dismiss, and it names the paragraphs
  the wrong answers in their last failing sitting came from along with what
  opening them does. The sitting they already have is left alone and no model
  call is made while they are held.
- **Presence is enough.** Every paragraph on the reading surface carries the
  event that reports it being opened, fired as it scrolls into view, and the
  citation links record it too for a student reading without JavaScript.
  Scrolling to the end is not required: a rule that can be satisfied without
  understanding still works, and one that requires understanding cannot be
  checked by a server at all.
- **Unlocking is immediate.** Opening the last paragraph the refusal names is
  the moment the next press is allowed; nothing is queued and nothing is waiting
  to be recalculated. The same rule covers submitting the same quiz again, or
  the block would be a speed bump past the other button.

`SectionOpen` is one row per paragraph, kept however many times it is opened:
what it records is that the student has been there, and a paragraph read after
two failures stays read for the third.

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