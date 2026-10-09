from django.urls import path

from material import views

urlpatterns = [
    path("", views.course_list, name="course-list"),
    path("courses/new/", views.course_create, name="course-create"),
    path("courses/<int:course_id>/", views.course_detail, name="course-detail"),
    path(
        "courses/<int:course_id>/read/",
        views.read_course,
        name="course-read",
    ),
    path(
        "courses/<int:course_id>/topics/",
        views.topic_path,
        name="topic-path",
    ),
    path(
        "courses/<int:course_id>/topics/infer/",
        views.topic_infer,
        name="topic-infer",
    ),
    path(
        "courses/<int:course_id>/schedule-check/",
        views.schedule_check,
        name="schedule-check",
    ),
    path(
        "courses/<int:course_id>/topics/add/",
        views.topic_add,
        name="topic-add",
    ),
    path(
        "courses/<int:course_id>/topics/confirm/",
        views.topic_confirm,
        name="topic-confirm",
    ),
    path(
        "courses/<int:course_id>/topics/<int:topic_id>/rename/",
        views.topic_rename,
        name="topic-rename",
    ),
    path(
        "courses/<int:course_id>/topics/<int:topic_id>/weight/",
        views.topic_reweigh,
        name="topic-reweigh",
    ),
    path(
        "courses/<int:course_id>/topics/<int:topic_id>/split/",
        views.topic_split,
        name="topic-split",
    ),
    path(
        "courses/<int:course_id>/topics/<int:topic_id>/merge/",
        views.topic_merge,
        name="topic-merge",
    ),
    path(
        "courses/<int:course_id>/topics/<int:topic_id>/move/",
        views.topic_move,
        name="topic-move",
    ),
    path(
        "courses/<int:course_id>/topics/<int:topic_id>/remove/",
        views.topic_remove,
        name="topic-remove",
    ),
    path(
        "courses/<int:course_id>/original/<int:file_id>/",
        views.source_original,
        name="source-original",
    ),
    path(
        "slides/<int:slide_id>/image/<str:name>",
        views.slide_image,
        name="slide-image",
    ),
]
