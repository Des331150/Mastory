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
