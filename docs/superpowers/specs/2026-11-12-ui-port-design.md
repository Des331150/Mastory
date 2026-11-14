# Porting the locked prototype screens into the real Django templates

Date: 2026-11-12
Status: approved spec, ready for planning

## Intent and why

Yesterday's prototype pass produced ten approved screens — the product's design
language, decided and locked screen by screen against the real views. The work
was done so that the real app would wear it. Right now the dev server serves the
old design: `material/templates/material/` (15 templates + one shared
`reading.css`) predates the pass. This port is that work paying off: the locked
screens become the working UI, feature-complete against the existing views.

## The locked screens (recovered from the 2026-11-11 session)

| Screen | Locked prototype(s) |
|---|---|
| 1 · Signup | `prototypes/signup/03-quiet-product.html` |
| 2 · Course list | `prototypes/courses/03-focus.html` + `04-empty.html` (empty state) |
| 3 · Course creation/upload | `prototypes/course-new/01-quiet-form.html` (form → reading, metered note folded in) |
| 4 · Topic path | `prototypes/topic-path/01-checklist.html` |
| 5 · Week / plan | `prototypes/plan/01-timeline.html` + `03-behind.html` (behind state) |
| 6 · Reading | `prototypes/read/01-quiet-reader.html` (with `03`'s conversion strip + failed-file notice) |
| 7 · Quiz | `prototypes/quiz/01-sitting.html`, `02-marked.html`, `03-held.html` |
| 8 · Exam | `prototypes/exam/01-sitting.html`, `02-marked.html`, `03-not-written.html` |
| 9 · Readiness | `prototypes/readiness/03-one-card.html` + `02-refused.html` |
| 10 · Landing | `prototypes/landing/04-promise.html` (standalone marketing page; no porting needed) |

Design language (set by the signup screen, shared by all): ground `#f2f5f1`,
ink `#17281f`, green `#0e7c5b` reserved for progress, Gabarito for display,
Hanken Grotesk for body, calm panels, honest captions.

## Approaches considered

1. **CSS-only restyle** — keep the current template markup, swap the stylesheet.
   Cheapest, but the locked screens differ structurally (week timeline, one-card
   readiness, session cards), so most pages would still be their old shape
   wearing new colours. Rejected: it is not the thing that was approved.
2. **Full port: templates rebuilt to the locked markup, shared language moves
   into `base.html` + `reading.css`** — what was approved. Every view, form,
   URL and behaviour keeps working; the 484 HTTP-seam tests stay green as the
   proof. **Chosen.**
3. **Rebuild as a separate front-end (SPA/HTMX rewrite)** — rejected: against
   the existing app's grain, and the prototypes were designed feature-complete
   *against* these views.

## Decisions made with the human partner

- **Exam gains the locked behaviour, not just the look.** The locked
  `exam/01-sitting.html` paginates the paper: three questions per page, a pager,
  and the rule that answers stay on the page until the whole paper is handed
  in. The real exam becomes one form whose pages are shown and hidden
  client-side (Alpine, already vendored) — nothing is marked or sent until
  hand-in, so the existing submission/marking contract is untouched.
- **Cost page gets prototypes first.** No locked prototype exists. Two or three
  variants in the locked language will be built and presented for a pick, then
  ported. It must not read as a foreign page next to the rest.
- **The copy rules from the `unslop` skill apply** to every sentence written
  during the port (the user asked for this during the prototype pass).

## Constraints

- All 484 tests in `material.tests` must pass unchanged — the templates are
  restyled, the contracts are not. (The exam pagination is the one behaviour
  change; it must keep "one form, nothing sent until hand-in" true.)
- Every ported page renders clean at 1200px and 390px.
- Fonts: the prototypes load Gabarito + Hanken Grotesk from Google Fonts; the
  app should load them the same way (no vendoring unless it proves broken).
- htmx + Alpine stay; no new front-end dependencies.

## What's left to the builder

- Order of work and the per-template mapping (planned next).
- Small copy deltas where a prototype caption doesn't map 1:1 onto real state
  the template already renders — follow the unslop rules, keep the honest
  captions' voice.
- The cost-page prototype variants (to be picked before its port).
