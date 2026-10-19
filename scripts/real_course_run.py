"""One real course, run from upload to readiness report, with everything written down.

Issue 14 asks for a course nobody chose to be easy: the actual file, the actual
model, the whole path, and a record of every place it hurt. This drives the real
application through the same HTTP seam the test suite uses, times each step, and
writes one JSON file of findings for the write-up to be written from.

It is not a test. Nothing here asserts anything: a run that finds the product
broken is the run working. It also refuses to fabricate - a step it could not
complete is recorded as the refusal it got, with the words the student would
have read, because a findings document that quietly leaves out the step that
broke is the one artefact nobody can rely on.

    .venv/bin/python scripts/real_course_run.py <course.pdf> <findings.json>

The model comes from the environment, as it does everywhere else in Mastory.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

from material import model as model_module  # noqa: E402
from material.models import (  # noqa: E402
    Attempt,
    MockExam,
    MockQuestion,
    Plan,
    Question,
    Quiz,
    SectionOpen,
    Session,
    Slide,
    SourceFile,
    Span,
    Spend,
    Topic,
    TopicPath,
    TopicSlide,
)

# What the student answered when nothing sensible can be derived. A real student
# gets some right, so a fixed mostly-wrong answer is the honest stand-in for a
# run of this kind: it produces a mark, it produces missed paragraphs to send
# back to, and it does not flatter the product.
WRONG = "this is not the answer and is deliberately wrong"

findings: dict[str, Any] = {
    "run_started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "source_pdf": None,
    "steps": [],
    "extraction": {},
    "topics": {},
    "verification": {},
    "pointer_map": {},
    "cost": {},
    "timing": {},
}


def note(step: str, **detail: Any) -> None:
    findings["steps"].append({"step": step, **detail})
    print(f"[{step}] {json.dumps(detail, default=str)}", flush=True)


def text_of(response: Any) -> str:
    """The words on the page, with the markup taken out, so a record of what the
    student was shown can be read later without a browser."""
    body = response.content.decode("utf-8", "replace")
    lowered = body.lower()
    for tag in ("<script", "<style"):
        start = 0
        while (index := lowered.find(tag, start)) != -1:
            end = lowered.find(">", index)
            start = end + 1
            body = body[:index] + body[end + 1 :]
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    pdf = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    findings["source_pdf"] = str(pdf)
    findings["source_bytes"] = pdf.stat().st_size
    findings["model"] = {
        "base_url": os.environ.get("MASTORY_MODEL_BASE_URL", ""),
        "name": os.environ.get("MASTORY_MODEL_NAME", ""),
        "usd_per_million_tokens": os.environ.get(
            "MASTORY_MODEL_USD_PER_MILLION_TOKENS", ""
        ),
    }

    import pymupdf

    with pymupdf.open(str(pdf)) as document:
        findings["source_pdf_pages"] = int(document.page_count)

    client = Client()

    # --- upload ------------------------------------------------------------
    started = time.monotonic()
    with pdf.open("rb") as handle:
        response = client.post(
            "/courses/new/",
            {"files": [handle], "title": f"REAL RUN: {pdf.stem}"},
        )
    findings["timing"]["upload_seconds"] = round(time.monotonic() - started, 2)
    note(
        "upload",
        status=response.status_code,
        redirect=response.headers.get("Location"),
        seconds=findings["timing"]["upload_seconds"],
        says=text_of(response)[:400],
    )
    if response.status_code != 302:
        findings["outcome"] = "upload refused"
        out.write_text(json.dumps(findings, indent=2, default=str))
        return 1

    course_id = int(response.headers["Location"].strip("/").split("/")[1])

    # --- what ingestion made of it ----------------------------------------
    files = list(SourceFile.objects.filter(course_id=course_id))
    findings["extraction"]["files"] = [
        {
            "name": f.original_name,
            "status": f.status,
            "failure_reason": f.failure_reason,
            "failure_advice": f.failure_advice,
            "page_count": f.page_count,
            "stages": f.stages,
        }
        for f in files
    ]
    slides = list(Slide.objects.filter(source_file__course_id=course_id).order_by("number"))
    spans = Span.objects.filter(slide__in=slides)
    blank_slides = [s.number for s in slides if not s.plain_text.strip()]
    thin_slides = [
        s.number for s in slides if 0 < len(s.plain_text.strip()) < 120
    ]
    ocr_only = [s.number for s in slides if not s.markdown.strip()]
    findings["extraction"].update(
        {
            "slides_stored": len(slides),
            "spans_stored": spans.count(),
            "slide_numbers_missing": sorted(
                set(range(1, max((s.number for s in slides), default=0) + 1))
                - {s.number for s in slides}
            ),
            "slides_with_no_text": blank_slides,
            "slides_with_under_120_chars": thin_slides,
            "slides_with_no_markdown": ocr_only,
            "images_extracted": sum(len(s.images) for s in slides),
            "longest_slide_chars": max((len(s.plain_text) for s in slides), default=0),
        }
    )
    note("extraction", **findings["extraction"])

    # --- the topic path ----------------------------------------------------
    started = time.monotonic()
    response = client.post(f"/courses/{course_id}/topics/infer/")
    findings["timing"]["topic_infer_seconds"] = round(time.monotonic() - started, 2)
    topics = list(Topic.objects.filter(course_id=course_id).order_by("position"))
    findings["topics"] = {
        "count": len(topics),
        "flagged": [
            {"title": t.title, "note": t.flag_note, "slides": _slides(t)}
            for t in topics
            if t.flagged
        ],
        "titles": [{"title": t.title, "weight": t.weight, "slides": _slides(t)} for t in topics],
        "infer_seconds": findings["timing"]["topic_infer_seconds"],
        "infer_page_says": text_of(response)[:600],
    }
    note("topics", count=len(topics), flagged=len(findings["topics"]["flagged"]))
    findings["pointer_map"] = {
        "topic_slide_rows": TopicSlide.objects.filter(topic__course_id=course_id).count(),
        "per_topic": [
            {
                "title": t.title,
                "slides": _slides(t),
                "span_count": Span.objects.filter(
                    slide__topic_pointers__topic=t
                )
                .distinct()
                .count(),
            }
            for t in topics
        ],
    }

    # --- schedule ----------------------------------------------------------
    exam_date = (date.today() + timedelta(days=28)).isoformat()
    started = time.monotonic()
    response = client.post(
        f"/courses/{course_id}/topics/confirm/", follow=True
    )
    settings_response = client.post(
        f"/courses/{course_id}/plan/settings/",
        {"days_per_week": "4", "hours_per_week": "8", "exam_date": exam_date},
        follow=True,
    )
    findings["timing"]["schedule_seconds"] = round(time.monotonic() - started, 2)
    plan = Plan.objects.filter(course_id=course_id).first()
    findings["schedule"] = {
        "exam_date": exam_date,
        "mode": getattr(plan, "mode", None),
        "sessions": Session.objects.filter(plan=plan).count() if plan else 0,
        "confirm_says": text_of(response)[:300],
        "settings_status": settings_response.status_code,
        "settings_says": text_of(settings_response)[:400],
    }
    note("schedule", **findings["schedule"])

    # --- a quiz on every topic --------------------------------------------
    per_topic_quiz: list[dict[str, Any]] = []
    for topic in topics:
        started = time.monotonic()
        response = client.get(f"/courses/{course_id}/topics/{topic.pk}/quiz/")
        seconds = round(time.monotonic() - started, 2)
        quiz = Quiz.objects.filter(topic=topic).order_by("-created_at").first()
        if quiz is None:
            per_topic_quiz.append(
                {
                    "topic": topic.title,
                    "built": False,
                    "seconds": seconds,
                    "page_says": text_of(response)[:300],
                }
            )
            continue
        questions = list(quiz.questions.order_by("ordinal"))
        entry = {
            "topic": topic.title,
            "built": True,
            "seconds": seconds,
            "dropped": quiz.dropped,
            "low_confidence": quiz.low_confidence,
            "kept": len(questions),
            "distinct_spans": len({q.span_id for q in questions}),
            "questions": [
                {
                    "prompt": q.prompt[:160],
                    "span": q.span.ordinal,
                    "slide": q.span.slide.number,
                    "cited_text": q.span.text[:160],
                }
                for q in questions
            ],
        }
        per_topic_quiz.append(entry)

        # Sit it, so mastery and the readiness report have something to read.
        response = client.post(
            f"/courses/{course_id}/topics/{topic.pk}/quiz/submit/",
            {
                "quiz": str(quiz.pk),
                **{f"q-{q.pk}": WRONG for q in questions},
            },
            follow=True,
        )
        entry["sitting_refused"] = 'class="error"' in response.content.decode()
    findings["verification"]["topic_quizzes"] = per_topic_quiz
    findings["verification"]["topics_with_no_quiz"] = [
        q["topic"] for q in per_topic_quiz if not q["built"]
    ]
    findings["verification"]["total_dropped"] = sum(
        q.get("dropped", 0) for q in per_topic_quiz
    )
    note(
        "topic_quizzes",
        built=sum(1 for q in per_topic_quiz if q["built"]),
        of=len(per_topic_quiz),
        dropped=findings["verification"]["total_dropped"],
    )

    # --- the mock exam -----------------------------------------------------
    started = time.monotonic()
    response = client.post(f"/courses/{course_id}/exam/start/")
    findings["timing"]["exam_start_seconds"] = round(time.monotonic() - started, 2)
    exam = MockExam.objects.filter(course_id=course_id).first()
    findings["verification"]["mock_exam"] = (
        {
            "written": True,
            "dropped": exam.dropped,
            "asked_topics": exam.asked_topics,
            "questions": MockQuestion.objects.filter(exam=exam).count(),
            "distinct_spans": len(
                {
                    mq.span_id
                    for mq in MockQuestion.objects.filter(exam=exam)
                }
            ),
            "questions_detail": [
                {
                    "prompt": mq.prompt[:160],
                    "topic": mq.topic.title if mq.topic_id else None,
                    "slide": mq.span.slide.number,
                }
                for mq in MockQuestion.objects.filter(exam=exam).order_by("ordinal")
            ],
        }
        if exam
        else {"written": False, "page_says": text_of(response)[:400]}
    )
    if exam:
        paper = list(MockQuestion.objects.filter(exam=exam).order_by("ordinal"))
        response = client.post(
            f"/courses/{course_id}/exam/submit/",
            {
                "exam": str(exam.pk),
                **{f"q-{mq.pk}": WRONG for mq in paper},
            },
            follow=True,
        )
        findings["verification"]["mock_exam"]["sitting_refused"] = (
            'class="error"' in response.content.decode()
        )
    findings["timing"]["exam_seconds"] = round(time.monotonic() - started, 2)
    note("mock_exam", **findings["verification"]["mock_exam"].get("written", False) and {
        "dropped": exam.dropped if exam else None,
        "questions": findings["verification"]["mock_exam"].get("questions"),
    } or {"written": exam is not None})

    # --- readiness and cost ------------------------------------------------
    response = client.get(f"/courses/{course_id}/readiness/")
    attempts = list(Attempt.objects.filter(topic__course_id=course_id))
    findings["readiness"] = {
        "status": response.status_code,
        "says": text_of(response)[:1500],
        "attempts": len(attempts),
        "mean_score": _mean([a.score for a in attempts]),
        "missed_pointers": sum(a.missed.count() for a in attempts),
        "paragraphs_sent_back_to": SectionOpen.objects.filter(
            span__slide__source_file__course_id=course_id
        ).count(),
    }
    response = client.get(f"/courses/{course_id}/cost/")
    rows = list(Spend.objects.filter(course_id=course_id).order_by("created_at"))
    findings["cost"] = {
        "rows": [
            {
                "kind": r.kind,
                "label": r.label,
                "calls": r.calls,
                "seconds": round(r.seconds, 2),
                "tokens": r.tokens,
            }
            for r in rows
        ],
        "total_calls": sum(r.calls for r in rows),
        "total_seconds": round(sum(r.seconds for r in rows), 2),
        "total_tokens": _sum_tokens(rows),
        "page_says": text_of(response)[:900],
    }
    findings["cost"]["by_kind"] = _by_kind(rows)
    note("cost", **{k: v for k, v in findings["cost"].items() if k != "rows"})

    # --- pointer map checked by hand --------------------------------------
    findings["pointer_map_check"] = _pointer_sheet(course_id)
    findings["pointer_map_check_note"] = (
        "One line per topic: the slides it points at, the first 90 characters of "
        "each of those slides, and a bare verdict left blank on purpose. Reading "
        "the verdict column against the real file is the hand check."
    )
    findings["outcome"] = "ran to the readiness report"
    findings["run_finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    out.write_text(json.dumps(findings, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


def _slides(topic: Topic) -> list[int]:
    return sorted(
        Slide.objects.filter(topic_pointers__topic=topic)
        .distinct()
        .values_list("number", flat=True)
    )


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _sum_tokens(rows: list[Spend]) -> int | None:
    total = 0
    for row in rows:
        if row.tokens is None:
            return None
        total += row.tokens
    return total


def _by_kind(rows: list[Spend]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = out.setdefault(row.kind, {"calls": 0, "seconds": 0.0, "rows": 0})
        bucket["calls"] += row.calls
        bucket["seconds"] = round(bucket["seconds"] + row.seconds, 2)
        bucket["rows"] += 1
    return out


def _pointer_sheet(course_id: int) -> list[dict[str, Any]]:
    """The pointer map laid out to be read against the real file by hand."""
    sheet = []
    for topic in Topic.objects.filter(course_id=course_id).order_by("position"):
        slides = list(
            Slide.objects.filter(topic_pointers__topic=topic)
            .distinct()
            .order_by("number")
        )
        sheet.append(
            {
                "topic": topic.title,
                "weight": topic.weight,
                "flagged": topic.flagged,
                "flag_note": topic.flag_note,
                "slides": [s.number for s in slides],
                "what_those_slides_say": [
                    {"slide": s.number, "opens_with": s.plain_text.strip()[:90]}
                    for s in slides
                ],
                "verdict": "",
            }
        )
    return sheet


if __name__ == "__main__":
    raise SystemExit(main())