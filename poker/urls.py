from django.urls import path

from . import views


app_name = "poker"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<int:pk>/", views.project_detail, name="project_detail"),
    path("projects/<int:pk>/tasks/import/", views.task_import, name="task_import"),
    path("projects/<int:pk>/sessions/new/", views.session_create, name="session_create"),
    path("projects/<int:pk>/sprints/new/", views.sprint_create, name="sprint_create"),
    path("sessions/<int:pk>/", views.session_manage, name="session_manage"),
    path("sessions/<int:pk>/start/", views.session_start, name="session_start"),
    path(
        "sessions/<int:pk>/queue/add/",
        views.session_queue_add,
        name="session_queue_add",
    ),
    path(
        "sessions/<int:pk>/tasks/<int:task_pk>/start/",
        views.session_start_task,
        name="session_start_task",
    ),
    path("sessions/<int:pk>/reveal/", views.session_reveal, name="session_reveal"),
    path("sessions/<int:pk>/revote/", views.session_revote, name="session_revote"),
    path("sessions/<int:pk>/accept/", views.session_accept, name="session_accept"),
    path("sessions/<int:pk>/finish/", views.session_finish, name="session_finish"),
    path("sessions/<int:pk>/state/", views.session_state, name="session_state"),
    path("room/<uuid:token>/", views.room, name="room"),
    path("room/<uuid:token>/join/", views.room_join, name="room_join"),
    path("room/<uuid:token>/state/", views.room_state, name="room_state"),
    path("room/<uuid:token>/vote/", views.room_vote, name="room_vote"),
    path("sprints/<int:pk>/", views.sprint_detail, name="sprint_detail"),
    path("sprints/<int:pk>/tasks/add/", views.sprint_add_tasks, name="sprint_add_tasks"),
    path(
        "sprints/<int:pk>/tasks/<int:task_pk>/remove/",
        views.sprint_remove_task,
        name="sprint_remove_task",
    ),
    path("sprints/<int:pk>/export/", views.sprint_export, name="sprint_export"),
]
