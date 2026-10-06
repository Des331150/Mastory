# v0: Personalised Learning Path Generator

A study planner for Ghanaian university students. The student uploads their course material, gets a topic path they can correct, a schedule anchored to their exam date, short grounded quizzes that point them back to the exact slide they got wrong, and a readiness report at the end.

The schedule is the product. Everything else exists to make the schedule hold.

Status: ready-for-agent

---

## Problem Statement

A Ghanaian university student has 200-plus slides for a course, an end-of-semester final that is effectively the whole grade, and about six weeks. They do not have a study problem or a content problem. They have a **commitment** problem: the material is available, it is even in their own possession, and they still do not finish it.

Everything they currently use makes this worse rather than better. A folder of PDFs means choosing what to study, every single session, which is the exact decision they keep avoiding. A notes app means transcribing instead of learning. A generic chatbot is confidently wrong and cites nothing. Past questions are scattered across group chats and are usually for the wrong year.

The three things that would actually help are the three things that do not exist:

1. **A defensible order.** Nothing tells them what to study today. Everything else follows from this.
2. **Honest feedback on whether it stuck.** Re-reading feels like learning. It is not. Only a question they can get wrong tells the truth.
3. **A pointer back to the source.** When they are stuck, "ask a chatbot" produces a plausible answer that may contradict their lecturer. "Page 47, third bullet" is worth more than anything a model can generate, because it is true by construction.

The result today: a student who plans nothing, studies what feels comfortable, walks into a final having covered maybe sixty percent of the course, and discovers the gap too late to fix.

## Solution

Upload your material. We extract the topics. You correct them, because you know your course better than any model does. You tell us when your exam is and how many days a week you can actually study. You get a weekly schedule that tells you what to do today and shows you what it cost you to miss a day. You study a topic, you take a four-question quiz, and when you get a question wrong you are sent to the exact slide it came from, with the question regenerated next time so you cannot memorise the answer. Every question is drawn from your material and cites it, and any question the system cannot verify against the source is dropped rather than shown.

At the end: a mock exam across the whole course, a per-topic mastery score, and a ranked list of your three weakest topics. Not a percentage that tells you nothing.

Two things this deliberately is not. It does not teach: when a student asks something the slides do not cover, the honest answer is "that is not in your slides, here is where it is covered in chapter four." And it does not compress. Missing sessions shift; they are never squeezed into fewer days, because a schedule the student already knows they cannot do is a schedule they abandon.

## User Stories

### Getting started

1. As a student, I want to upload my course slides as PDF files, so that my plan is built from the material I am actually being examined on rather than generic content.
2. As a student, I want to upload several files for one course at once, so that a course split across twelve weekly decks is one course and not twelve.
3. As a student, I want to see readable progress while my files are being processed, so that I trust a four-minute wait more than an anonymous spinner.
4. As a student, I want to be told how far along the extraction is in terms I understand, so that I know it is working and not hung.
5. As a student, I want one unreadable file to fail without losing the other four, so that a single bad scan does not cost me my evening.
6. As a student, I want to be told why a file failed and what to do about it, so that I can fix it myself instead of asking for help.
7. As a student, I want to be told to stop waiting and split the file myself if processing takes too long, so that I am not stuck watching a spinner for nine minutes.
8. As a student, I want to keep the original file I uploaded, so that if the rendered version loses a diagram or an equation I can always check the source.
9. As a student, I want the app not to re-download material I already have, so that I do not burn my data bundle re-reading the same content.

### The topic path

10. As a student, I want to see the list of topics my material covers, so that I know what I am actually being asked to learn.
11. As a student, I want the app to work out those topics from my slides even when the slides have no headings, so that I get a plan from a messy lecture deck.
12. As a student, I want to see topics in the order my course teaches them, so that the plan matches how the exam is sequenced.
13. As a student, I want to rename a topic that is named badly, so that the plan speaks my course's language.
14. As a student, I want to split one topic into two, so that a chapter containing three unrelated ideas becomes three study sessions.
15. As a student, I want to merge two topics that are really one, so that a session is not artificially tiny.
16. As a student, I want to reorder topics, so that I can put what I am weakest at first.
17. As a student, I want to change how much time a topic is allocated, so that a forty-slide topic does not get the same slot as a three-slide one.
18. As a student, I want to add a topic I know is missing, so that the plan covers what the lecturer emphasised verbally.
19. As a student, I want to remove a topic that will not be examined, so that I do not spend a session on it.
20. As a student, I want to be able to tell that a topic was inferred unreliably, so that I check it against my slides before I trust the plan.
21. As a student, I want to be required to confirm the topic path before a schedule is generated, so that I never commit to a plan built on a bad extraction.
22. As a student, I want to correct the topic path after a bad extraction, so that one bad topic does not ruin the whole week.

### Planning

23. As a student, I want to enter my exam date, so that the plan has something to work backwards from.
24. As a student, I want to set how many days a week I can study, so that the plan fits my life instead of replacing it.
25. As a student, I want to set how many hours a week I can study, so that the plan is achievable on a normal week, not a perfect one.
26. As a student, I want to be offered fewer sessions rather than more, so that I am not handed a plan I already know I will fail.
27. As a student, I want to prioritise my weakest topics myself, so that the trade-off between topics is mine to make.
28. As a student, I want one topic to be one session, so that a half-finished topic does not become a thing I dread.
29. As a student, I want a longer session for a topic with more material in it, so that the time I spend matches the work there is.
30. As a student, I want to see my whole week at a glance, so that I know what I am committing to.
31. As a student, I want to see today's session without navigating to it, so that starting is one tap.
32. As a student, I want the app to work before I know my exam date, so that I can start in week three of semester when there is no exam in sight.
33. As a student, I want no countdown when my exam date is not set, so that the app is not pressuring me about a date I do not have.
34. As a student, I want the plan to become exam-anchored once I enter my exam date, so that the intensity increases when there is a reason for it.
35. As a student, I want the sessions I completed and missed shown as a proportion, so that I can see whether I am keeping up without a streak punishing one bad day.
36. As a student, I want missed sessions to shift to later days rather than pile up, so that falling behind does not immediately become impossible.
37. As a student, I want to be told the real date I will finish if I keep my current pace, so that the app is honest with me when I am behind.
38. As a student, I want to choose which topics to cut when I am behind, so that I am making the scope decision with the deadline in view.
39. As a student, I want to see what cutting a topic costs me, so that my choice is an informed one.
40. As a student, I want a session marked skipped with one action and no guilt, so that a bad day does not turn into a broken streak and an abandoned app.

### Studying

41. As a student, I want to read my material in a clean, readable layout rather than a raw PDF, so that forty minutes of reading is actually bearable.
42. As a student, I want the reading view to load fast on a slow connection, so that I do not wait a minute for every page.
43. As a student, I want to search within my material, so that I can find the one passage I half remember.
44. As a student, I want to see the original file one click away from the rendered version, so that I can check anything that looks mangled.
45. As a student, I want to jump straight to the specific section a question came from, so that I am not hunting through forty slides to find it.
46. As a student, I want the jump to land on the right paragraph, so that the pointer is worth trusting.

### Quizzes

47. As a student, I want a short quiz after each topic I study, so that checking myself is quick enough to always do.
48. As a student, I want every quiz question to come from my own slides, so that I am practising the material I will be examined on.
49. As a student, I want to see which slide each question came from, so that I can verify the question was not invented.
50. As a student, I want to be told when a question is not answerable from my slides rather than being given an invented answer, so that I do not memorise something false.
51. As a student, I want to see how confident I am that the quiz reflects my actual course, so that I know whether to trust my score.
52. As a student, I want to see an honest short quiz rather than a padded one when the material supports few questions, so that I am not drilled on invented content.
53. As a student, I want to be able to retake a quiz as many times as I need, so that getting it wrong does not end my session.
54. As a student, I want different questions on a retake, so that I am learning the material rather than memorising answers.
55. As a student, I want to be sent back to the cited slide when I fail twice, so that I re-read the right thing instead of the whole topic.
56. As a student, I want to be made to actually open the section I was sent to before a third attempt, so that I cannot click past the point of the feature.
57. As a student, I want to be told what will unlock my retry rather than being silently refused, so that the block does not feel arbitrary.
58. As a student, I want a topic marked done once I have completed it, so that finishing is achievable even on a hard topic.
59. As a student, I want mastery tracked separately from finishing, so that completing a topic does not falsely claim I know it.
60. As a student, I want a later good attempt to count more than an early one, so that my score reflects where I am now.
61. As a student, I want topics I have not finished to stay visible in the plan, so that nothing is silently dropped.
62. As a student, I want a short daily quiz on the current topic, so that attending to the plan is rewarded even on a low-energy day.

### Mock exam and readiness

63. As a student, I want a mock exam across the whole course, so that I can test whether I am actually ready rather than assume it.
64. As a student, I want the mock exam to look like a real exam, so that it tells me something true about exam conditions.
65. As a student, I want one attempt at the mock exam and no regeneration, so that it is a test rather than a drill.
66. As a student, I want no feedback until I finish the mock exam, so that I am not reading ahead to the answers.
67. As a student, I want the mock exam to point me back to the slide for anything I got wrong, so that I know exactly what to revise.
68. As a student, I want a mastery score per topic, so that I can see where I stand topic by topic.
69. As a student, I want to see how many times I attempted each topic, so that a high score from one lucky attempt is distinguishable from real knowledge.
70. As a student, I want my weakest topics ranked and named, so that I know what to revise first.
71. As a student, I want to be told when there is not enough data for a reliable readiness figure, so that I am not misled by a confident-looking percentage.
72. As a student, I want the readiness report to be something I can screenshot and share with my course-mates, so that recommending the app has an artefact behind it.
73. As a student, I want to see the dates I actually studied, so that I can tell whether my habits are real.
74. As a student, I want to know I have finished the course, so that the app has an ending for me rather than running indefinitely.

### Grounding and honesty

75. As a student, I want every generated question traceable to a specific span of my material, so that I can always check it.
76. As a student, I want questions my material cannot support to be removed, so that I never study something that is not on the exam.
77. As a student, I want to be told when my material is too poor to generate from, so that I know to fix my upload instead of blaming myself for a bad score.

## Implementation Decisions

### Product shape

- The schedule is the product. Content features are means to it, and any feature that does not make the student more likely to complete tomorrow's session is not in v0.
- The app is a **companion**, not a burst tool. A student's exam cycle justifies nothing in week six of a semester, so the app is useful across the whole semester, not just the run-up.
- **One course at a time.** A student activates a course, sets availability, works it, and must explicitly finish or abandon it before starting another. Concurrent multi-course is out.
- The target user is a Ghanaian university student preparing for a single high-stakes end-of-semester final.
- Students read primarily on a laptop. The reading surface is optimised for that. Mobile still matters for the queue and the quiz, so the app is responsive on both and must degrade gracefully on a slow connection. Offline is not in v0.

### Grounding

- **The student's slides are the source of truth.** The system never generalises, never fills a gap from world knowledge, and never teaches outside the supplied material. When a student asks something their slides do not contain, the correct response names where it *is* covered, or says it is not covered. This is the product's whole advantage: a general chatbot can explain Gaussian elimination, but it cannot cite page 47 of *this* student's notes.

### Topics

- A topic is **AI-inferred**, using the deck's own outline as a hint where one exists. Slide decks frequently have no structure, so the outline cannot be the source of truth.
- A topic is the unit of the schedule, the quiz, mastery, and targeted re-reading. It must be able to point at several non-contiguous slides; **that pointer map is the core asset of the product.**
- Topic weight is derived from extraction signal: span count, slide count, presence of equations or images, and low-confidence flags.
- The student can rename, split, merge, reorder, re-weight, add and remove topics **before** generating a schedule. Edits are persisted.
- The student must confirm the topic path before a schedule exists. A wrong topic path the student fixed in thirty seconds is acceptable; one discovered during finals is fatal.

### Ingestion

- Synchronous, with no job queue and no background workers. Upload returns rendered material.
- **Named progress stages** rather than a percentage-only spinner: pages read, topics inferred, source verified.
- **Per-file failure isolation.** One unreadable file never fails the whole upload; the successful files ingest and the failure is reported with its cause and a suggested action.
- A hard timeout of two minutes per file, returning a clear explanation rather than an indefinite wait.
- **Extraction is cached by file content hash.** A course's material is extracted once regardless of how many students upload it. This is the difference between a per-student cost that scales linearly and one that does not.
- The original uploaded file is **retained alongside** extracted Markdown. Re-extraction must be possible without asking the student to re-upload, and a mangled diagram must always be checkable against the source.

### Reading surface

- **Markdown-first, with extracted images preserved inline.** Chosen over a bespoke layout parser (weeks of work, highest risk in the build) and over embedding a raw PDF viewer (ships fast, but the product becomes a PDF player).
- Markdown gives fast load on a slow connection, real search, a citation target that maps to an anchor, and a text-only degradation path.
- Extraction of complex diagrams and equations may degrade. This is accepted, mitigated by the retained original, and by the confidence flag below.

### Quizzes and grading

- Two quiz types only. **Topic quiz**: short, retryable, regenerating, drives completion and mastery. **Exam**: whole-course, scored, single attempt, no regeneration, no feedback until submitted. A weekly quiz is not a third type; it is a topic quiz the schedule happens to trigger.
- Grading is **multiple choice and short answer only**, auto-graded by key match. The limit is explicit in the product rather than hidden. One grader, honestly scoped, beats four badly-scoped ones.
- Grading never silently contradicts the source. Every question carries its originating span, and a graded answer is always resolvable back to it.

### Generation pipeline

- **Span first, then question.** Extract a span, generate questions from that span alone, attach a citation, then run a cheap verification pass against the source span.
- Verification failure is a **silent drop**, not a surfaced error. A topic with four verifiable questions gets a four-question quiz; a topic with one is flagged low-confidence. The quiz is never padded to hit a count.
- Regeneration after two failed attempts on the same topic.
- The third attempt is **blocked until the cited section is opened.** Presence, not comprehension: a cheap event fires when the target section scrolls into view, and the server records it. A soft dismissable panel would make the feature decoration; a hard block is what makes it work.
- All model calls sit behind **one module** with a single grounding contract.

### Scheduling

- **Weighted topics, one topic per session, sessions only.** No topic splits across days; partial sessions are where abandonment starts.
- Session count derives from total topic weight against the weekly budget, so a forty-slide topic gets more time than a three-slide one.
- **Missed sessions shift, never compress.** Redistributing missed work into fewer days produces a plan the student already knows is impossible.
- When behind, show the true projected finish date and let the student **choose what to cut**, with the cost of cutting visible. Honesty plus agency, rather than either alone.
- **Two modes.** *Exam mode* with a date: weighted plan, redistribution, readiness report. *Open mode* without: topics in course order, a rolling next-session card, no countdown, no readiness claim, no projection. Open mode is what makes the app worth opening in a week with no exam in sight.
- Progress is reported as **completion percentage and session log, not a streak.** A streak breaks on a single missed day, punishing exactly the student the shift-never-compress model is designed to protect.

### Readiness

- Per topic: **completion** (binary, gates the schedule, passes at any score) and **mastery** (0–1 from the best scored attempt, with later attempts weighted more heavily).
- Course readiness is the weighted mean of topic mastery, and the app does not present it as "the exam will be easy" but as "these are your weakest topics".
- **Insufficient data is stated.** With mastery data for four of twenty topics, readiness is unmeasurable and the app says so rather than printing a number.
- The final artefact is the **mock exam**, with the mastery table and weakest-topic ranking as supporting output.

### Data model

```
Course -> Files -> Slides -> Spans      extraction layer
Course -> Topics -> TopicSlides          the pointer map
Topic  -> Questions -> Citations         generation layer
Topic  -> Attempts                        event log
Schedule -> Slots -> Topic               planning layer
```

- Topics are **per-student records**. No shared topic library in v0. This accepts that no two students' paths match, in exchange for not building a layer nothing validates yet.
- Every table carries a `user_id`, so adding real authentication in v1 is a filter rather than a refactor.

### Stack and architecture

- **Django, HTMX, Alpine.js, Postgres.** One language, one deployable, no frontend build step. Alpine is not a compromise; it covers the two interactions HTMX handles worst (drag-to-reorder topics, per-question quiz navigation state) in a handful of lines. Text-to-speech would be the Web Speech API, but it is cut.
- **All model calls live in one server-side module** enforcing the grounding contract. This is the single seam the test suite fakes.
- Ingest runs in the request. No queue, no workers, no streaming. Six weeks does not permit it.
- **v0 has one user with a hardcoded identity.** Every query is nonetheless written as if `user_id` existed, so v1 adds auth without touching domain logic.
- **All model calls are on demand, never speculative.** No pre-generating a week's quizzes, no generating a mock exam until asked. Every generated artefact is cached by content hash so reopening is free.
- Supabase-hosted Postgres, one project, one bill. Supabase **Auth is not used**; Django's own auth serves the single hardcoded user, and swapping auth providers later costs more than it saves.

### Instrumentation

Log from day one, because none of it can be backfilled:

- Per topic: attempts with timestamp, score, and verification-pass count.
- Whether the cited section was actually opened before a retry was unlocked. This is the mechanic that makes the re-reading feature work, and it is the leading indicator of anything the product does.
- Per course: slots completed versus skipped.

The success metric is the proportion of a student's topics crossing a mastery threshold. It is **derived from this event data, not self-reported**; the student's post-exam impression is optional confirmation, not the measurement. With one user, no access to their real exam, and six weeks, the honest claim is that the instrumentation exists and the metric is not yet readable.

## Testing Decisions

**One seam: HTTP.** Every test drives the real Django application through the Django test client against a real database. Upload a PDF, read the material, edit topics, generate a schedule, take a quiz, read the readiness report. Assertions are on what the student sees and can do.

- **Assert external behaviour only.** A test asserts that a third attempt is refused, not that a particular function was called. A test asserts the student sees four questions when four survived verification, not that the verifier returned four.
- **The LLM is faked at one boundary.** All model calls sit behind a single module, so a deterministic fake lives at that existing boundary and no HTTP test knows it exists. This is not a second seam; it is a fake at the one place the system talks to a model.

What the fake makes deterministically assertable:

- extraction and topic inference produce the expected topics from a known fixture
- a question whose answer cannot be verified against its span is **dropped**, and a short quiz is returned rather than a padded one
- a topic with only one verifiable question is flagged low-confidence
- a third attempt is refused until the cited section's open event is recorded, and permitted immediately after
- questions regenerate after two failed attempts on the same topic
- behind-schedule redistribution shifts later sessions and never compresses them
- a single unreadable file fails alone while its siblings ingest
- readiness refuses to produce a figure when mastery data covers too few topics

Deliberately **not** tested at this seam: whether the real model is any good. That is a manual check against the real course, and its output is a signal rather than a test. Writing an assertion against non-deterministic output produces a test that fails for reasons nobody can act on.

Modules tested at the HTTP seam: ingestion, topic path and student edits, scheduling, topic quiz with citation jump and retry policy, exam, readiness reporting, and session tracking. Nothing is unit-tested for its own sake; if a behaviour cannot be observed through the HTTP seam, it is not behaviour this product promises.

## Out of Scope

Cut deliberately. Each was in the original feature list; none is load-bearing for v0.

- **Chatbot.** Contradicts the source-of-truth constraint. Would be a search box with a chat skin, and would require abandoning grounding to be useful.
- **Fetching external resources.** Contradicts the source of truth by construction.
- **Forums and course rooms.** Private user-owned uploads are never republished between students, so there is no shared corpus for such a feature to stand on. This also removes the moderation and legal surface entirely.
- **Gamification and streaks.** Progress is completion percentage and a session log. No points, no leaderboard, no streak counter.
- **Desktop and native applications.** Responsive web only.
- **Text-to-speech.** Cut, though Markdown anchors make it a small future addition.
- **Integrations with other software.**
- **Equation and diagram semantics.** Accepted degradation, flagged rather than hidden.
- **Image-only scans with no text layer.** OCR for digital PDFs is in; pure image scans are not.
- **Concurrent multi-course.** One active course at a time, by design.
- **Shared or cross-student topic libraries.** Topics are per-student records.
- **Course catalogue and syllabus-scoped topics.** Topics come from the uploaded material only.
- **Authentication and multi-user support.** One hardcoded user; `user_id` present on every table so it is additive later.

## Further Notes

### The six-week sequence

A vertical slice each week, each making the previous one more of something real. The failure mode this avoids is three weeks of ingestion pipeline with nothing to show.

| Week | Deliverable |
|---|---|
| 1 | Upload one PDF, convert to Markdown with images, read it on a laptop. Nothing else. |
| 2 | Topic path inferred, student can correct it. Multi-file messy ingest is a stretch. |
| 3 | Schedule generation, both modes, weighted sessions. |
| 4 | One topic, one grounded and verified quiz, citation jump, retry policy. |
| 5 | Completion and mastery tracking, readiness report. |
| 6 | Mock exam, seeded data, one real course run end to end. |

Week 1 is deliberately the riskiest single component, because reading is where the student spends most of their time and a v0 that is a raw PDF viewer is a v0 that has not solved anything.

### Open risks

- **Markdown conversion fidelity.** The highest build risk. Complex layouts and multi-column slides may convert badly. Mitigation: the original is always retained and one click away, and low-confidence topics are flagged.
- **Extraction accuracy.** A wrong topic path produces a wrong plan and erodes trust in week one. Mitigation: the student reviews and confirms before any schedule exists. This is the single most important safety valve in the design.
- **Per-student cost.** Ingestion plus two to three generations a week is affordable for one user and unproven at scale. Mitigation: cache by content hash, generate on demand only, and instrument cost from day one so there is a number to price against.
- **Assessment validity.** There is no way to check that the generated quizzes predict real exam performance in six weeks with one student. The instrumentation is in place to make that measurable later; the claim is not yet supportable.

### The two quiz types

Topic quiz and exam are the whole set. Everything that looked like a third type is one of these two triggered at a different time. Each quiz type needs its own UI, schedule interaction, attempt record and grading rule, so the count is the discipline.

### Relationship to the original feature list

Fifteen original features reduce to five in v0: ingest and render, topic path, schedule, topic quiz with targeted re-reading, mock exam with readiness. Ten are cut. The reduction is the point: the original list described a content-transformation suite, and the product that survived the grilling is a planner that happens to read.