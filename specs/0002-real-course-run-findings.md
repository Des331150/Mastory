# 0002: What the first real course did

Issue 14 asked for one real course, run from upload to readiness report, with
every failure written down. This is that document. It sits next to
`0001-v0-personalised-learning-path.md` because it is the first evidence about
whether that spec survives contact with real material.

**No run completed the path the issue asked for.** Four were made; the best
reached the readiness report with no mock exam written. That is the first thing
to know about everything below, and it is repeated wherever it bears on a
number rather than left as a caveat.

Two defects were found and fixed here, because they stopped the run getting past
the first screen. The third, and everything the run could not measure, are
listed at the end.

## What was actually run

**University of Ghana, Handbook for the Bachelor's Degree, Volume 4: Course
Descriptions for Programmes in the Health Sciences, September 2017.** 233 pages,
1,325,354 bytes, `sha256 4b0f3219e61573489b9ee5846f46b4fc4406cb9bd8592fe22b4250de24ce0077`,
from `https://www.ug.edu.gh/sites/default/files/manual_uploads/CHS handbook 2017.pdf`.

### This is not the file the issue asked for, and the difference matters

Issue 14 asked for *one course's slides*. What was run is a **multi-programme
course-description handbook**: 233 pages describing fourteen BSc programmes
across six schools, plus the university's own front matter and academic
calendar. It is not one assessed course.

Ghanaian university lecture decks of 200-plus pages are not published openly. The
KNUST institutional repository (2,740 hits) and UG's were searched first; both
hold theses, journal articles and handbooks, not lecture series. This was the
best available real file over 200 pages, and it is a **different and harder kind
of test in some ways and an easier one in others**:

- Harder for extraction: no headings, no slide numbers, a two-column contents
  page, administrative front matter interleaved with syllabi, dense tables.
- Harder for quiz generation: mostly course *administration* — who holds what
  post, what a semester is, how many credits a course carries — which resists
  being turned into a well-posed question.
- **Easier** than a real slide deck on several axes: text density, no diagrams,
  no equations, and a stable single-column reading order.

So nothing here should be read as "this is what happens when a student uploads
their MATH 201 slides." It shows that the product survives contact with one
adversarial real document and that its external dependencies do not.

### Reproduction

Extraction is deterministic, so the file converts identically on any machine
with these versions. **The model calls do not reproduce** — a second run gets
different topics, different questions and different drops, because the model is
sampled and the provider's quota state differs.

| | |
|---|---|
| Commit | `1aa8be5` on `issue-14-real-course-run` |
| Python / Django | 3.14 / 6.1.1 |
| pymupdf / pymupdf4llm | 1.28.2 / 1.28.2 |
| Database | SQLite (the test seam's own database) |
| Model | `gemini-3.5-flash-lite` via Google's OpenAI-compatible endpoint, free tier |
| Priced at | $0.30 per million tokens, a blended rate — see Cost |

The run evidence is retained at [`run-evidence/run-c.json`](run-evidence/run-c.json),
including the pointer map with a verdict filled in against each topic. Every
figure below is readable from it.

### How it was driven

[`scripts/real_course_run.py`](../scripts/real_course_run.py) drives the real
application through the Django test client — the same HTTP seam the test suite
uses — and writes one JSON file of findings. **It asserts nothing.** A run that
finds the product broken is the run working. It is not a test and is not part of
the suite.

```sh
.venv/bin/python scripts/real_course_run.py <course.pdf> <findings.json>
```

Four runs were made, differing only in model and timeout:

| Run | Model | Timeout | Result |
|---|---|---|---|
| A | gemini-2.5-flash | 60 s (default) | Topic inference timed out. No topic path. |
| B | gemini-2.5-flash | 600 s | Inference answered; reply discarded as unparseable. No topic path. |
| C | gemini-3.5-flash-lite | 600 s | 16 topics, 16 sessions, 13/16 quizzes, **exam refused**. |
| D | gemini-3.8-flash | 600 s | 16 topics, 1/16 quizzes, 995 s burned. |

Run C is used as the reference because it got furthest. **That is survivor
selection, not replication**: the model, the timeout, the quota state and the
code all changed between runs, so before-and-after is not controlled. Any claim
below that depends on a single run says so.

## Extraction

| | |
|---|---|
| Pages in | 233 |
| Slides stored | 233 |
| Slide numbers missing | none |
| Pages with no text | 0 |
| Pages with no Markdown | 0 |
| Pages under 120 characters | 1 (page 233, the back page) |
| Spans cut | 3,118 |
| Images extracted | 1 |
| Longest page | 3,901 characters |
| **Wall clock, upload → readable** | **95.0 s** (82.7 / 95.0 / 98.7 / 102.1 / 104.6 across runs) |

**The acceptance criterion asked for extraction failures to be recorded with the
slides and spans that failed. None occurred.** The recording path exists —
`SourceFile.failure_reason`, `failure_advice` and the three-stage log — and was
read on this run and was empty at every stage: `pages_read`, `converting`,
`verifying` all reported `done`. What the run *did* establish is narrower than
"extraction held": every page produced a row with non-empty text, and every
slide number 1–233 is present.

That is a row-count and non-blank check. It is not a fidelity check. Nobody
compared the extracted Markdown against the PDF for tables, reading order,
equations or figures, and no OCR-dependent page was in this file at all, so
**scanned and image-heavy course material remains entirely untested.**

One defect the run did surface: converted pages carry their structural tags into
the stored Markdown as literal text, and from there into the plain text the
model is shown.

```
UNIVERSITY OF GHANA <!-- Start of picture text -- wen<br<!-- End of picture text -- VOLUME 4
```

### The timeout margin is small and variable

Ingestion took 82.7–104.6 s across four runs against
`INGEST_FILE_TIMEOUT_SECONDS` = **120 s**. That is a margin of between 14% and
30% on a 233-page file, varying by 26% between runs of the same bytes.

At the fastest observed rate this would reach the limit at roughly 340 pages,
and at the slowest at roughly 270. **This is an extrapolation and no 300-page
file was run**, and per-page cost depends on image density and layout rather
than scaling linearly. The supported claim is: *this file left a small and
variable margin under a fixed limit*. Whether a 300-slide deck is abandoned
depends on the deck.

## Topic inference

16 topics over 233 slides, confirmed, and a 16-session exam plan built over 4
days a week, 8 hours a week, exam 28 days out. The planner did its job on real
material — but note that "confirmed" here means the harness posted the
confirmation endpoint. **No student reviewed, corrected, renamed, split, merged
or re-weighted anything.** The plan was built on an unchecked path.

### The prompt is the first bill and the first failure

`material/model.py` sends **every slide in one call**, each trimmed to 1,200
characters (`material/topics.py:118`, `material/model.py:181`).

| | |
|---|---|
| Slides sent | 233 |
| Prompt size | **305,235 characters (~76k tokens)** |
| Outline headings found | 91 |
| Slides hitting the 1,200-character cap | **227 of 233** |
| Course text discarded before the model sees it | **354,653 characters — 56% of the file** |

Run A took **60.69 s against the 60 s `MODEL_TIMEOUT_SECONDS` default** and
produced nothing: no topic path, and a student told to add topics by hand. Run C
answered the same request in **5.72 s** on a different model.

So the honest reading is not "a 200-plus page course produces no topic path".
It is: **one call carrying the whole course sits right on the default timeout,
and whether it fits depends entirely on which model answers it.** That is a
deployment hazard with no warning — the single call that reads the whole course
in is the one most likely to hit the wall, and nothing in the product says so.

The 56% discard is a separate and more certain problem: a topic whose
distinguishing material sits past 1,200 characters on a slide cannot be inferred
from that slide at all.

### The flagged-topic mechanism never fired

`CONFIDENCE_FLOOR` is 0.6. Below it a topic is flagged so the student checks it
before trusting the plan — acceptance criterion 20, and the affordance the topic
page rests on.

**Every topic from every model came back at confidence 1.0. Zero flagged across
four runs and two model families.** The one defence the product claims against a
bad inference is switched off by any model that reports uniform certainty, and
there is no second signal behind it — nothing checks whether a topic's title
agrees with the paragraphs it points at.

### Could a student correct it in under a minute? Not measured

Nobody was timed. The interface for correcting is one click per operation, and
sixteen topics is a short list, but **whether a student could spot the errors in
under a minute is untested and this document does not claim it either way.**

What can be said is structural: with no flag and no cross-check, a student has
no way to know *which* topics to spend the minute on, and would have to check
all sixteen against their slides.

## Verification

**A measurement problem that stops the acceptance criterion being answered.**

Issue 14 asked for questions dropped by verification to be recorded. The product
stores one number, `Quiz.dropped`, and `material/quiz.py:237` documents on
purpose that it **blends** two causes:

```python
dropped=written.dropped + len(written.questions) - len(kept)
```

Verification rejections *and* questions that verified but were cut because a
quiz holds four. The page is meant to be able to say what it left out, and it
cannot say which. **The two causes cannot be separated from anything the product
persists**, so no verification drop rate can be computed from a run. The harness
now records the column under its real name, `dropped_as_stored`, alongside
`at_the_cap`, precisely so that this is visible rather than mistaken for a rate.

What the per-topic figures *do* support, because `kept < 4` with
`dropped_as_stored == 0` means the generator simply wrote too few:

| | Run C, 16 topics |
|---|---|
| Topics given a quiz | 13 |
| Topics given none at all | 3 |
| Quizzes below the four-question target | 9 of 13 |
| Of those, **wrote fewer than four and dropped none** | **7** |
| Topics at the four-question cap | 4 |
| Topics whose quiz was empty | 1 |
| Sittings recorded | 12 |
| Mean score (every answer deliberately wrong) | 0.0 |

**So the shortfall is mostly generation, not verification.** Seven topics
produced one to three questions and lost nothing. The model is writing too few
questions for administrative course descriptions — which is what the drops look
like when read in the logs:

```
dropped a short answer question with no answer: 'What is the dominant technique
employed in the LANG 001: WRITING SKILLS I course?'
dropped a question whose answer is not in its span: 'What course provides
background understanding of human bodily structures?'
dropped an ungrounded question claim: '' on span 23
```

The last is a question with an **empty prompt**, returned repeatedly, each one
costing a call to discover.

Whether a one-question quiz is *usable* is a judgement this document does not
make on the run's behalf. The defensible statement is: **nine of thirteen
topics fell below the four-question target the spec describes, and seven of
those fell short because the generator under-produced, not because verification
rejected.**

### Three topics got nothing at all

Not dropped by grounding — the provider was throttling, the verification calls
failed, and every question was lost. See Cost. The student cannot tell this
apart from a grounding drop, because it is recorded identically.

## The pointer map, checked by hand

Every topic's slides were opened and compared with its title. The completed
sheet, with a verdict against each topic, is in
[`run-evidence/run-c.json`](run-evidence/run-c.json) under
`pointer_map_check`. **14 ok, 1 wrong, 1 thin; none left unchecked.**

**1. "College of Health Sciences Overview and Administration" — wrong.** Cites
slides 1, 2, 3, 4, 27, 28. Slides 1–4 are the title page, the copyright page,
the note to undergraduates, and the **table of contents**. The topic title names
only the second half, and the contents page belongs to no topic at all. A
student quizzed here gets questions about how the book is laid out. In run B the
model created a separate topic called "Table of Contents"; in run C it folded
one into this. Neither is right.

**2. "Academic Calendar" — thin.** Three pages, six spans in total. Correctly
named, but too small to support a quiz; it produced two questions.

The non-contiguity of topic 1 — slides 1–4 and 27–28, a 22-slide gap — is
correct behaviour and is exactly why pointers are join rows. The failure is in
**naming, not pointing**, and nothing in the product notices.

This is also the case that argues against the reviewers' suggested remedy of
checking a title's words against its slides: a table of contents sitting inside a
named topic is not evidence that inferred abstractions are unsafe.

## Cost

Priced at $0.30 per million tokens, a blended rate the run was configured with.
**Tokens are what the provider reported; nothing here is estimated from request
sizes.**

Run C:

| Work | Calls | Seconds | Tokens | Cost |
|---|---|---|---|---|
| Reading the file in | 0 | 94.2 | 0 | $0.000000 |
| Working out the topic path | 1 | 5.7 | 73,270 | **$0.021981** |
| Writing the 10 quizzes that both wrote and verified fully | 40 | 63.0 | 34,353 | **$0.010306** |
| 3 quizzes partly written, throttled | 23 | 116.4 | unknown | unknown |
| Writing the mock exam, which failed | 35 | 95.3 | unknown | unknown |
| **Total (16 quiz rows + 1 exam row + 1 topic row + 1 ingest row)** | **99** | **374.6** | **unknown** | **≥ $0.032** |

The quiz column is 63 calls across 16 rows; the table shows only the 40 of those
whose token count the provider reported. The remaining 23 are calls that were
made and refused.

**One call to infer the topic path cost more than the twelve quizzes whose cost
is known, put together** — $0.021981 against $0.010306. That is a comparison of
*known* costs only, and it is the most important number here, because it is the
one piece of work that scales with course size while the per-quiz work does not.
It is **not** a claim that inference is the most expensive work in the product:
four quiz rows and the exam row have no token count, and if those four quizzes
cost anything near $0.022 each the comparison reverses. What survives is
narrower and still worth acting on — *one call reading the whole course in cost
more than every quiz the run managed to write at a known price.*

**The total is not a real figure and the acceptance criterion is not met.** Three
quiz rows and the exam row have no token count, because those calls were
refused, and `Ledger.tokens` returns `None` for the whole course rather than
pricing what it knows. That is correct behaviour and it means **the cost of one
full course run has still not been measured.** It could not be, on a free tier.

### The free tier cannot serve this product

- This run made **99 model calls and still did not finish** — the exam was never
  written. A complete run needs *more* than 99; how much more is unknown,
  because the exam never ran. Gemini's free tier allows about 20 requests a
  minute plus a daily cap per model.
- Runs A and B exhausted `gemini-2.5-flash`'s daily quota. It then refused with
  *"Please retry in 22h16m"*.
- `gemini-2.5-flash-lite`, `gemini-2.0-flash` and `gemini-3.5-flash-lite`
  returned **404** on the OpenAI-compatible endpoint. Only the `-latest` aliases
  and the 3.x models are reachable there.
- Run C made 99 calls and was throttled three times even with backoff.
- Run D made 37 calls in 995 seconds and built **1 of 16 quizzes** — slower and
  worse than run C.

Before the fix in this branch, throttling was reported as *"The language model
could not be reached"*, which sends a student to check their connection instead
of to wait, and lost every question in the affected topic silently.

## What was fixed here

**A correct reply inside a Markdown code fence was discarded.** Every reply was
parsed with a bare `json.loads`. Gemini fences its JSON most of the time. Run B
returned a complete, valid, 24-topic reply inside a ```` ```json ```` fence and
every claim in it was dropped for three backticks, logged only as "model reply
was not JSON". One `_decoded` helper now unwraps a fence that is the whole
reply; a reply with prose around the JSON is still refused, because guessing
where the answer ends is worse than refusing.

**Throttling was read as unreachability and was not waited for.** `complete`
now retries a 429, believes `Retry-After` in **both** the seconds and HTTP-date
forms, backs off, and reports the model as too busy rather than unreachable.
The waiting is bounded by `RETRY_BUDGET_SECONDS` (45) on the **total**, not per
sleep — a provider asking for a minute on every refusal would otherwise buy four
minutes of a student watching a spinner, and the page only ever asks for one.

Four things changed after review, all worth stating:

- **The ledger now counts HTTP attempts, not intentions.** A call throttled and
  retried is several calls, because several requests were made and throttling
  counts against a quota. The cost of such a call is honestly *unknown* — the
  refused requests reported no token count — so the row says so rather than
  pricing one known answer as a total.
- **The error holding a refused response is now closed.** `HTTPError` is
  file-like and can own the socket it came from; a throttled run makes these by
  the dozen, and they were being left for the collector.
- **A body that is not text is now unreachable rather than a crash.** A
  non-UTF-8 reply escaped as a raw `UnicodeDecodeError`, and the spend row then
  recorded zero calls for a request that was in fact made. `Retry-After: nan`
  had the same shape: it parsed as a float, compared false to everything, and
  raised inside `sleep`.
- **The harness stopped lying.** It used to label every non-aborted run "ran to
  the readiness report" regardless of what happened, and wrote its JSON only at
  the end, so a crash lost the whole run. It now writes after every step through
  a temporary file and `replace()`, so an interrupted write cannot destroy the
  evidence it exists to keep; it flushes per topic rather than per stage, so a
  run that dies on topic twelve still has eleven; it traps and records the step
  that killed it; and it computes its outcome from what actually happened,
  checking the readiness and schedule responses rather than assuming them. Run
  C's honest outcome is `partial: no mock exam written`.

Tests: 438 passing. mypy strict: clean.

## What must change before a second student is onboarded

Ordered by what blocks the most.

**0. There is no second student.** `material/users.py` returns a constant
`HARDCODED_USER_ID`; there is no authentication in v0. **A second student in the
same deployment is the first student** and sees and modifies their courses.
Everything scoped by `user_id` is ready for real identity, but that identity does
not exist. This blocks a second student before any of the below does.

**1. The topic-inference prompt has to stop being the whole course.**
One 76k-token call, 56% of the material discarded to fit, sitting on the default
timeout. Chunk it: a first pass over titles, then the slides of each candidate
topic. Biggest cost, biggest latency risk, and the reason run A produced nothing.

**2. Rate limiting must be a state the product knows, not a dropped question.**
Three of sixteen topics had no quiz and the readiness report cannot tell that
from a grounding drop. A topic with no verified questions should say so.

**3. `Quiz.dropped` cannot answer "what did verification reject?"**
Issue 14 asked for that and the product cannot produce it. Store the two causes
separately.

**4. The generator under-produces on administrative material.** Seven of
thirteen quizzes came up short with nothing dropped. This material resists
well-posed questions, and the right answer may be an explicit "not enough
assessable material here" rather than a thinner quiz — **but that is a
hypothesis from one handbook and must be tested on real lecture decks before it
becomes a gate.**

**5. The flagged-topic mechanism is inert.** Confidence 1.0 from every model
means criterion 20 never fires and the student is never warned. This needs a
second signal that does not depend on the model's self-report.

**6. Ingestion has a 14–30% margin on a 233-page file, varying 26% run to run.**
Scale the limit with page count, or raise the default.

**7. Throttling copy leaks onto the wrong page.** The mock exam's refusal says
*"You can add the topics yourself below"* — on a page about an exam. The advice
is shared across every model failure; it should be chosen per page.

**8. The full cost of a full run is unknown.** It needs a paid key and one
patient run. Until then the cost page can honestly say what each piece cost and
cannot say what a student costs.

## The one-line version

Extraction produced every page. Generation never finished the journey, and the
three things that stopped it — one oversized call, one provider's rate limits,
and an unverifiable drop count — are all still open. The first thing to change
is the 76k-token call that reads the whole course in a single request.