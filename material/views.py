"""The student-facing views: upload, read, search, and the retained original.

Every query is scoped by the hardcoded user's ``user_id``.
"""

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.db.models import QuerySet
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from material.models import Course, Slide, SourceFile
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