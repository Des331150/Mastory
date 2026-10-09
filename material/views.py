"""The student-facing views: upload, read, search, the retained original, and
the topic path the student corrects.

Every query is scoped by the hardcoded user's ``user_id``.
"""

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.db.models import Field, QuerySet
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from material import model, planning, topics as topic_service
from material.models import Course, Plan, Slide, SourceFile, Topic
from material.render import Section, build_sections
from material.services import (
    StageOutcome,
    UploadRejected,
    ingest_uploads,
    stage_reports,
)
from material.users import current_user_id, owned


@dataclass(frozen=True)
class Document:
    source_file: SourceFile
    sections: list[Section]
    stages: list[StageOutcome]


def _owned_or_404(queryset: QuerySet[Any], *, pk: int, what: str) -> Any:
    row = owned(queryset).filter(pk=pk).first()
    if row is None:
        raise Http404(f"No such {what}")
    return row


def _course(course_id: int) -> Course:
    course: Course = _owned_or_404(Course.objects.all(), pk=course_id, what="course")
    return course


def _topic(course_id: int, topic_id: int) -> Topic:
    course = _course(course_id)
    topic: Topic = _owned_or_404(
        course.topics.all(), pk=topic_id, what="topic"
    )
    return topic


def _documents(course: Course, query: str) -> list[Document]:
    documents = []
    for source_file in owned(course.files.all()):
        sections = build_sections(
            owned(source_file.slides.all()), query=query, image_url=_image_url
        )
        documents.append(
            Document(
                source_file=source_file,
                sections=sections,
                stages=stage_reports(source_file),
            )
        )
    return documents


def _image_url(slide: Slide, name: str) -> str:
    return reverse("slide-image", args=[slide.pk, name])


def _default_title(original_name: str) -> str:
    """A readable course title from the first of the uploaded filenames."""
    stem = Path(original_name).stem.replace("-", " ").replace("_", " ").strip()
    return stem[:1].upper() + stem[1:]


def _query_from(request: HttpRequest) -> str:
    return request.GET.get("q", "").strip()


@require_GET
def course_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "material/course_list.html",
        {"courses": owned(Course.objects.all())},
    )


@require_POST
def course_create(request: HttpRequest) -> HttpResponse:
    uploads = request.FILES.getlist("files")
    if not uploads:
        return render(
            request,
            "material/course_create.html",
            {"error": "Choose a PDF to upload."},
            status=200,
        )

    first_name = Path(str(uploads[0].name)).name
    title = request.POST.get("title", "").strip() or _default_title(first_name)

    try:
        course = ingest_uploads(
            user_id=current_user_id(),
            title=title,
            uploads=uploads,
        )
    except UploadRejected as exc:
        return render(
            request,
            "material/course_create.html",
            {"error": str(exc)},
            status=200,
        )

    destination = reverse("course-read", args=[course.pk])
    if request.headers.get("hx-request"):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = destination
        return response
    return redirect(destination)


@require_GET
def course_detail(request: HttpRequest, course_id: int) -> HttpResponse:
    course = _course(course_id)
    return render(
        request,
        "material/course_detail.html",
        {"course": course, "files": owned(course.files.all())},
    )


@require_GET
def read_course(request: HttpRequest, course_id: int) -> HttpResponse:
    course = _course(course_id)
    query = _query_from(request)
    documents = _documents(course, query)
    sections = [section for d in documents for section in d.sections]
    return render(
        request,
        "material/read.html",
        {
            "course": course,
            "documents": documents,
            "sections": sections,
            "query": query,
            "matching": [s for s in sections if s.matched],
            "failures": [
                document for document in documents
                if document.source_file.status == SourceFile.Status.FAILED
            ],
        },
    )


@require_GET
def source_original(
    request: HttpRequest, course_id: int, file_id: int
) -> FileResponse:
    course = _course(course_id)
    source_file = _owned_or_404(
        course.files.all(), pk=file_id, what="file"
    )
    return FileResponse(
        source_file.original.open("rb"),
        content_type="application/pdf",
        as_attachment=False,
        filename=source_file.original_name,
    )


@require_GET
def slide_image(request: HttpRequest, slide_id: int, name: str) -> FileResponse:
    slide = _owned_or_404(Slide.objects.all(), pk=slide_id, what="slide")
    if name not in slide.images:
        raise Http404("No such image")
    path = slide.image_path(name)
    if not path.exists():
        raise Http404("No such image")
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return FileResponse(path.open("rb"), content_type=content_type)


def _topics_page(
    request: HttpRequest, course: Course, *, error: str = "", notice: str = ""
) -> HttpResponse:
    """The topic path, in the order the student will study it.

    Rendered straight from the database rather than cached in the page, so a
    reload after an edit is the same page the student was just looking at.
    """
    return render(
        request,
        "material/topics.html",
        {
            "course": course,
            "topics": list(owned(course.topics.all())),
            "slides": topic_service.course_slides(course),
            "confirmed": topic_service.is_confirmed(course),
            "error": error,
            "notice": notice,
        },
    )


@require_GET
def topic_path(request: HttpRequest, course_id: int) -> HttpResponse:
    """What the student sees, and what they change it into."""
    course = _course(course_id)
    return _topics_page(request, course)


@require_GET
def schedule_check(request: HttpRequest, course_id: int) -> HttpResponse:
    """Whether a schedule could be built right now, and why not if it could not.

    The gate the schedule ticket will generate through, exposed so that the
    refusal is something the student can be shown and tested rather than a rule
    that only exists in a service.
    """
    course = _course(course_id)
    try:
        topic_service.require_confirmation(course)
    except topic_service.Unconfirmed as exc:
        return render(
            request,
            "material/schedule_blocked.html",
            {"course": course, "reason": exc.reason},
            status=409,
        )
    return render(
        request,
        "material/schedule_ready.html",
        {"course": course, "topics": owned(course.topics.all())},
    )


@require_POST
def topic_infer(request: HttpRequest, course_id: int) -> HttpResponse:
    """Work the topic path out from the material, replacing what is there."""
    course = _course(course_id)
    try:
        topic_service.infer(course)
    except (topic_service.Rejected, model.ModelUnavailable) as exc:
        return _topics_page(request, course, error=_reason(exc))
    return redirect("topic-path", course_id=course.pk)


@require_POST
def topic_add(request: HttpRequest, course_id: int) -> HttpResponse:
    """A topic the student knows is missing."""
    course = _course(course_id)
    try:
        topic_service.add(course, request.POST.get("title", ""))
    except topic_service.Rejected as exc:
        return _topics_page(request, course, error=exc.reason)
    return redirect("topic-path", course_id=course.pk)


@require_POST
def topic_rename(request: HttpRequest, course_id: int, topic_id: int) -> HttpResponse:
    """A topic named the way the student's course names it."""
    topic = _topic(course_id, topic_id)
    try:
        topic_service.rename(topic, request.POST.get("title", ""))
    except topic_service.Rejected as exc:
        return _topics_page(request, topic.course, error=exc.reason)
    return redirect("topic-path", course_id=topic.course.pk)


@require_POST
def topic_reweigh(request: HttpRequest, course_id: int, topic_id: int) -> HttpResponse:
    """How much of the student's week this topic is worth."""
    topic = _topic(course_id, topic_id)
    given = request.POST.get("weight", "").strip()
    try:
        weight = int(given) if given else None
    except ValueError:
        return _topics_page(
            request, topic.course, error="Give the time as a whole number of units."
        )
    topic_service.reweigh(topic, weight)
    return redirect("topic-path", course_id=topic.course.pk)


@require_POST
def topic_split(request: HttpRequest, course_id: int, topic_id: int) -> HttpResponse:
    """One topic that is really two study sessions."""
    topic = _topic(course_id, topic_id)
    after = _owned_or_404(
        Slide.objects.all(), pk=_int_or_none(request.POST.get("slide")), what="slide"
    )
    try:
        topic_service.split(topic, after, request.POST.get("title", ""))
    except topic_service.Rejected as exc:
        return _topics_page(request, topic.course, error=exc.reason)
    return redirect("topic-path", course_id=topic.course.pk)


@require_POST
def topic_merge(request: HttpRequest, course_id: int, topic_id: int) -> HttpResponse:
    """Two topics that turn out to be one."""
    topic = _topic(course_id, topic_id)
    other = _owned_or_404(
        topic.course.topics.all(),
        pk=_int_or_none(request.POST.get("into")),
        what="topic",
    )
    try:
        topic_service.merge(topic, other)
    except topic_service.Rejected as exc:
        return _topics_page(request, topic.course, error=exc.reason)
    return redirect("topic-path", course_id=topic.course.pk)


@require_POST
def topic_move(request: HttpRequest, course_id: int, topic_id: int) -> HttpResponse:
    """One step up or down the path, for a student reordering by hand."""
    topic = _topic(course_id, topic_id)
    ordered = [t.pk for t in owned(topic.course.topics.all())]
    index = ordered.index(topic.pk)
    step = 1 if request.POST.get("direction") == "down" else -1
    target = index + step
    if 0 <= target < len(ordered):
        ordered[index], ordered[target] = ordered[target], ordered[index]
        topic_service.reorder(topic.course, ordered)
    return redirect("topic-path", course_id=topic.course.pk)


@require_POST
def topic_remove(request: HttpRequest, course_id: int, topic_id: int) -> HttpResponse:
    """A topic that will not be examined."""
    topic = _topic(course_id, topic_id)
    topic_service.remove(topic.course, topic)
    return redirect("topic-path", course_id=topic.course.pk)


@require_POST
def topic_confirm(request: HttpRequest, course_id: int) -> HttpResponse:
    """The student has checked the path. Only then may a schedule be built."""
    course = _course(course_id)
    try:
        topic_service.confirm(course)
    except topic_service.Rejected as exc:
        return _topics_page(request, course, error=exc.reason)
    return _topics_page(
        request,
        course,
        notice="Topic path confirmed. You can now plan your revision.",
    )


@require_GET
def plan_view(request: HttpRequest, course_id: int) -> HttpResponse:
    """The student's week, and the card for the session they start next.

    Refuses on an unconfirmed topic path the same way the schedule gate does,
    because this page is a schedule: showing a week of sessions over topics the
    student has not checked would be the one failure this product cannot have.
    """
    course = _course(course_id)
    try:
        topics = topic_service.require_confirmation(course)
    except topic_service.Unconfirmed as exc:
        return _schedule_blocked(request, course, exc.reason)
    return _plan_page(request, course, topics=topics)


@require_POST
def plan_settings(request: HttpRequest, course_id: int) -> HttpResponse:
    """Record what the student can manage and rebuild their week from it."""
    course = _course(course_id)
    try:
        topics = topic_service.require_confirmation(course)
        planning.generate(course, planning.read_availability(request.POST))
    except topic_service.Unconfirmed as exc:
        return _schedule_blocked(request, course, exc.reason)
    except topic_service.Rejected as exc:
        return _plan_page(
            request, course, topics=topics, error=exc.reason, entered=request.POST
        )
    return redirect("plan", course_id=course.pk)


def _schedule_blocked(request: HttpRequest, course: Course, reason: str) -> HttpResponse:
    return render(
        request,
        "material/schedule_blocked.html",
        {"course": course, "reason": reason},
        status=409,
    )


def _plan_page(
    request: HttpRequest,
    course: Course,
    *,
    topics: list[Topic],
    error: str = "",
    entered: QueryDict | None = None,
) -> HttpResponse:
    """The week, the next session, and the form that built them.

    The plan is read in one pass by ``planning.overview`` so that the weeks, the
    next session and the countdown on this page are all answers about the same
    read of the plan rather than three reads that could disagree.

    A form that came back with an error shows what the student typed rather
    than what was last saved. Being told your own hours are impossible and then
    finding them silently replaced is how a student stops trusting the form.
    """
    plan = planning.plan_for(course)
    return render(
        request,
        "material/plan.html",
        {
            "course": course,
            "plan": plan,
            "today": planning.today(),
            "overview": planning.overview(plan, topics) if plan is not None else None,
            "entered": entered or _saved_settings(plan),
            "error": error,
        },
    )


#: The three things the student tells the planner, in the order the form asks
#: for them.
SETTINGS = ("exam_date", "days_per_week", "hours_per_week")


def _default_setting(name: str) -> Any:
    """What the form starts at, read off the field rather than the template."""
    field: Field[Any, Any] = Plan._meta.get_field(name)  # type: ignore[assignment]
    return field.get_default()


def _saved_settings(plan: Plan | None) -> dict[str, str]:
    """What the form shows when the student has not just typed something.

    The starting numbers live on ``Plan`` rather than in the template, so the
    number the form offers and the number the schema would use are one answer
    rather than two that can drift. A date that is not set shows as nothing,
    which is what an empty date input needs.
    """
    values = [
        getattr(plan, name) if plan is not None else _default_setting(name)
        for name in SETTINGS
    ]
    return {
        name: "" if value is None else str(value)
        for name, value in zip(SETTINGS, values, strict=True)
    }


def _reason(exc: Exception) -> str:
    """What to tell the student, whether it came from us or from the model.

    A model failure carries advice as well as a reason - what to do about it -
    and the advice is the half that lets a student carry on without waiting for
    anyone.
    """
    reason = str(getattr(exc, "reason", exc))
    advice = getattr(exc, "advice", "")
    return f"{reason} {advice}" if advice else reason


def _int_or_none(raw: str | None) -> int:
    try:
        return int(raw or "")
    except ValueError:
        return 0