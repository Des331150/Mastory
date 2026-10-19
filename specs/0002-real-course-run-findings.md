# 0002: What the first real course did

Issue 14 asked for one real course, run from upload to readiness report, with
every failure written down. This is that document. It sits next to
`0001-v0-personalised-learning-path.md` because it is the first evidence about
whether that spec survives contact with real material.

Three defects were found and two were fixed before the run could finish. The
third, and the cost and shape findings, are still open and are listed at the
end.

## The file

**University of Ghana, Handbook for the Bachelor's Degree, Volume 4: Course
Descriptions for Programmes in the Health Sciences, September 2017.**
233 pages, 1.29 MB, downloaded from
`https://www.ug.edu.gh/sites/default/files/manual_uploads/CHS handbook 2017.pdf`.

One caveat stated up front, because it changes what the findings mean. Issue 14
asked for *one course's slides*. Ghanaian university course material of 200-plus
pages is not published as open lecture decks — the KNUST and UG institutional
repositories were searched first and hold theses, journal articles and course
handbooks, not lecture series. This file is a real University of Ghana course
document over 200 pages, and it is a harder case than slides rather than an
easier one: it has no headings, no slide numbers, a table of contents that
contradicts the page order, administrative front matter mixed with syllabi, and
several pages of tables. Where a finding depends on this being a handbook rather
than a deck, it says so.

Extraction is deterministic, so the file and the findings reproduce on any
machine with the same dependencies. The model calls do not: a second run gets
different topics, different questions and different drops.

## How the run was driven

`scripts/real_course_run.py` drives the real application through the Django test
client — the same HTTP seam the test suite uses — and writes one JSON file of
findings. It asserts nothing. A run that finds the product broken is the run
working. It is not a test and is not part of the suite.

```sh
.venv/bin/python scripts/real_course_run.py <course.pdf> <findings.json>
```

Four runs were made. They differ because each one found something:

| Run | Model | Timeout | Outcome |
|---|---|---|---|
| A | gemini-2.5-flash | 60 s (default) | Topic inference timed out. 0 topics. |
| B | gemini-2.5-flash | 600 s | Inference answered; reply discarded as unparseable. 0 topics. |
| C | gemini-3.5-flash-lite | 600 s | 16 topics, 16 sessions, 13/16 quizzes, exam refused. |
| D | gemini-3.8-flash | 600 s | 16 topics, 1/16 quizzes, 995 s burned. |

**Run C is the reference run.** The figures below are from it unless stated.

## Extraction

| | |
|---|---|
| Pages in | 233 |
| Slides stored | 233 |
| Slide numbers missing | none |
| Pages with no text at all | 0 |
| Pages with no Markdown | 0 |
| Pages under 120 characters | 1 (page 233, the back page) |
| Spans cut | 3,118 |
| Images extracted | 1 |
| Longest page | 3,901 characters |
| **Wall clock, upload → readable** | **95.0 s** (runs A–D: 98.7, 102.1, 82.7, 95.0, 104.6) |

**Extraction is the one part of this product that did not break.** Zero pages
lost, zero blank slides, on a messy 233-page document with mixed fonts, tables
and a two-column contents page. That is worth stating plainly because it is the
part the whole product rests on.

One thing the extractor leaves in the text: converted PDFs carry their
structural tags through into the stored Markdown as literal text.

```
UNIVERSITY OF GHANA <!-- Start of picture text -- wen<br<!-- End of picture text -- VOLUME 4
```

That is `pymupdf4llm`'s page-tearing detection being preserved rather than
stripped. It is visible to the student and it is in the plain text the model is
shown, so every page that had it carries those comments into generation.

### The timeout is closer than it looks

Ingestion took 82.7–104.6 s across four runs against a
`INGEST_FILE_TIMEOUT_SECONDS` default of **120 s**. The margin on a 233-page
file is between 14% and 30%. Per-page cost here was roughly 0.42 s, which puts
the 120-second limit at about **285 pages**.

A student with a 300-slide course — squarely inside the "200-plus slides" the
product is built for, and a normal size for a Ghanaian lecture series — is
abandoned at the default setting and told to split the file by hand. There is
no signal that this is close: the limit is a fixed constant, not a function of
page count, so the first course that crosses it is the first course anyone finds
out.

**Recommendation:** scale the limit with the page count, or raise the default to
a figure a real deck cannot exceed in one request.

## Topic inference

16 topics over 233 slides, confirmed, and a 16-session exam plan built over
4 days a week and 8 hours a week with the exam 28 days out. The planner is the
product and it did its job on real material.

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

Two failures in one call. It is too big to answer in time — **60.69 s against
the 60 s `MODEL_TIMEOUT_SECONDS` default**, so at default settings a real
200-plus page course produces no topic path at all and the student is told to
add topics by hand. And it is too big because 56% of the course was thrown away
to keep it smaller, which means a topic whose distinguishing material sits past
1,200 characters on a slide cannot be inferred from that slide.

Run C answered the same request in **5.72 s** on gemini-3.5-flash-lite. The
timeout is not a property of a large prompt on its own; it is a property of
this prompt on a particular provider. That is still a deployment hazard, because
the default is 60 seconds and nothing warns that the one call that reads the
whole course in is the one that will not fit.

### The flagged-topic mechanism never fired

`CONFIDENCE_FLOOR` is 0.6. A topic below it is flagged so the student checks it
before trusting the plan — acceptance criterion 20, and the affordance the whole
topic page rests on.

**Every topic from both models came back at confidence 1.0.** Zero topics
flagged across four runs. The one defence the product claims against a bad
inference is switched off by a model that reports uniform certainty, and there
is no second signal behind it: nothing checks whether a topic's slides agree
with its title.

This is the finding I would act on first. The grounding contract drops topics
that cite slides the material does not have, which is real protection, but it
does nothing about a topic whose slides are real and wrong.

### Can the student correct it in under a minute?

Sixteen topics is correctable in a minute. Sixteen topics *with no flag telling
you which are wrong* is not, because the student has to check all of them
against the slides. The interface for correcting is there — rename, split,
merge, reorder, re-weight — and each of those is one click. The cost is not the
click count; it is that nothing tells you where to spend it.

On this file, a student who did the check would have found the model's error in
the first topic (below) in under a minute, because the title says one thing and
the slides say another. They would have had no reason to look there first.

## Verification

The contract drops a question that cites a paragraph it was not written from,
a multiple-choice question whose answer is not among its own options, and a
short answer the paragraph does not contain. That contract did its job, hard.

Run C, 16 topics:

| | |
|---|---|
| Topics given a quiz | 13 of 16 |
| Topics given no quiz at all | 3 |
| Questions written and kept | 29 |
| Questions dropped | 18 |
| Sittings recorded | 12 |
| Mean score (deliberately wrong answers) | 0.0 |
| Wrong answers sent back to a paragraph | 32 pointers |

### The drop rate makes quizzes unusable

Kept questions per topic: 4, 2, 4, 1, 3, 3, 4, 4, 1, 1, 3, **0**, 2.

The spec promises a four-question quiz. **Eleven of the thirteen topics that got
a quiz got fewer than four questions, and six got one or two.** One topic got a
quiz with zero questions in it, and sitting it was refused.

So the honest answer to "did the topic still get a usable quiz" is: **mostly
no.** A one-question quiz cannot show a student what they do not know about a
topic, and it does not produce the "topic has been weighed" signal the readiness
report is built on. Twelve of sixteen topics were weighed. Four were not, and
the report cannot say why, because "nothing survived verification" and "the
provider was throttling" are recorded identically — as a smaller number.

The drops themselves are worth reading. They are overwhelmingly short-answer
questions the model wrote about content the span does not carry:

```
dropped a short answer question with no answer: 'What is the dominant technique
employed in the LANG 001: WRITING SKILLS I course?'
dropped a question whose answer is not in its span: 'What course provides
background understanding of human bodily structures?'
dropped an ungrounded question claim: '' on span 23
```

That last one is a question with an **empty prompt**. The model returned objects
with no text in them, repeatedly, and each one costs a call to discover. The
material here is mostly course *administrative* content — who holds what post,
what a semester is, how many credits a course carries — which is exactly the
material that resists being turned into a multiple-choice question with a
defensible answer.

The grounding contract is doing its job. The generator is asking the wrong
question of the wrong material, and the contract is what stands between the
student and a bad quiz.

### Three topics got nothing at all

Not dropped by grounding — the provider was throttling, the verification calls
failed, and every question was lost. See the next section.

## The pointer map, checked by hand

Every topic's slides read against the file. Fifteen of sixteen are correct and
sensibly contiguous. Two discrepancies:

**1. Topic 1 points at the table of contents.** "College of Health Sciences
Overview and Administration" cites slides 1, 2, 3, 4, 27, 28. Slides 1–4 are the
title page, the copyright page, the note to undergraduates, and the table of
contents. A student quizzed on this topic gets questions about how the book is
laid out.

The model also created a *separate* topic called "Table of Contents" in run B,
and dropped it in run C. Neither is right: the contents page belongs to no
topic, and the CoHS introduction deserves one of its own.

**2. Topic 1 spans a gap of 22 slides.** It holds slides 1–4 and 27–28. The
non-contiguity is correct behaviour — it is why pointers are join rows rather
than ranges — but combined with (1) it means one topic covers two unrelated
things, and the title names only one of them.

Also thin: "Academic Calendar" is 3 slides and 6 spans, and produced a
two-question quiz.

What this tells us: the pointer map is structurally sound and its unusual
coverage is usually right. The failure is in *naming*, not in pointing, and
nothing in the product notices a topic whose title does not describe its slides.

## Cost

Priced at the rate the run was configured with. Tokens are what the provider
reported; nothing here is estimated from request sizes.

Run C, gemini-3.5-flash-lite at $0.30 per million:

| Work | Calls | Seconds | Tokens | Cost |
|---|---|---|---|---|
| Reading the file in | 0 | 94.2 | 0 | $0.000000 |
| Working out the topic path | 1 | 5.7 | 73,270 | **$0.021981** |
| Writing 12 topic quizzes | 40 | 63.0 | 34,353 | **$0.010306** |
| 4 more quizzes, throttled | 23 | 116.4 | unknown | unknown |
| Writing the mock exam | 35 | 95.3 | unknown | unknown |
| **Total** | **99** | **374.6** | **unknown** | **≥ $0.032** |

**One call to infer the topic path costs more than every quiz on half the
course put together.** It is 73,270 tokens of the student's own slides, and it
happens once. That single figure is the most important cost number in this
document: the product's first call is also its most expensive one, and it scales
with course size while nothing else does.

The total is not a real figure. Three quiz rows and the exam row have no token
count, because those calls were refused, and `Ledger.tokens` returns `None` for
the whole course rather than pricing what it knows. That is the right behaviour
and it means **the cost of a full course run has still not been measured.** It
could not be, on a free tier.

### The free tier cannot serve this product

This is the most consequential finding in the document.

A full course run needs roughly **99 model calls**. Gemini's free tier allows
about 20 requests a minute and has a daily cap per model.

- Run A and B exhausted `gemini-2.5-flash`'s daily quota. It then refused with
  *"Please retry in 22h16m"*.
- `gemini-2.5-flash-lite`, `gemini-2.0-flash` and `gemini-3.5-flash-lite`
  returned **404** on the OpenAI-compatible endpoint. Only the `-latest` aliases
  and the 3.x models are reachable there.
- Run C used 99 calls and was throttled three times despite backoff.
- Run D on `gemini-3.8-flash` managed **37 calls in 995 seconds** and built
  **1 of 16 quizzes**. It was slower and worse than run C.

Before the fix in this branch, throttling was reported to the student as *"The
language model could not be reached"* — which sends a student to check their
connection rather than to wait. It also lost every question in the affected
topic silently, because the drop looked identical to a grounding drop.

## What was fixed in this branch

**A correct reply inside a Markdown code fence was discarded.**
`material/model.py` parsed every model reply with a bare `json.loads`. Gemini
answers a JSON request fenced three quarters of the time. Run B returned a
complete, valid, 24-topic reply wrapped in ```` ```json ````, and every claim in
it was dropped for the want of three backticks, logged only as "model reply was
not JSON". One `_decoded` helper now unwraps a fence that is the whole reply. A
reply with prose around the JSON is still refused, because guessing where the
answer ends is worse than refusing.

**Throttling was read as unreachability, and was not waited for.** `complete`
now retries a 429 up to five times, believes the provider's own `Retry-After`,
and otherwise backs off to a 60-second ceiling — the window a per-minute quota
actually needs — then says the model is too busy rather than unreachable. The
retries are one piece of work and one ledger row, not four.

Both are covered by tests. Full suite: 431 passing. mypy strict: clean.

## What must change before a second student is onboarded

Ordered by what blocks the most.

**1. The topic-inference prompt has to stop being the whole course.**
76k tokens, one call, 56% of the material discarded to fit, and a default
timeout it cannot meet. Chunk the outline by section, or send a first pass over
titles and a second pass over the slides of each candidate topic. This is the
single biggest cost, the single biggest latency risk, and the reason a
200-plus page course produces nothing at default settings.

**2. Rate limiting has to be a first-class state, not a dropped question.**
Three of sixteen topics had no quiz at all and the readiness report cannot
distinguish that from a grounding drop. A topic with no verified questions
should say so, and the cost ledger should show what was attempted.

**3. Verification is too strict to be useful on administrative material.**
A majority of topics got fewer than four questions; some got one. When
verification leaves a topic with one question, that is a finding about the
material and it should reach the student as a sentence, not as a thinner quiz.

**4. A topic's title is never checked against its slides.**
The only defence against a bad inference is a confidence number the model
reports as 1.0. The cheapest real check — does the topic's title appear in, or
follow from, the paragraphs it points at — is not implemented.

**5. A 233-page file has a 14–30% margin on the ingestion timeout.**
A 300-slide deck fails. The limit should scale with page count.

**6. The full cost of a full run is still unknown** because no run has completed
on a provider that would answer. It needs a paid key and one patient run.

**7. Throttling copy leaks onto the wrong page.** The mock exam's refusal says
*"You can add the topics yourself below"*, on a page about an exam. The advice
is shared between every model failure; it should be chosen per page.

## The one-line version

Extraction held. Inference did not, and the two things standing between a
student and a usable plan — a bounded prompt and a working model — are both
external limits rather than code. The fixes made here unblock a run; they do
not make the product work on a 200-plus page course, and the first thing to
change is the 76k-token call that reads the whole course in one request.