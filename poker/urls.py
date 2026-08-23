from django.urls import path

from . import views


app_name = "poker"

urlpatterns = [
    path(
        "organizer/invite/<uuid:token>/",
        views.organizer_invitation_accept,
        name="organizer_invitation_accept",
    ),
    path("", views.dashboard, name="dashboard"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<int:pk>/", views.project_detail, name="project_detail"),
    path("projects/<int:pk>/tasks/import/", views.task_import, name="task_import"),
    path(
        "projects/<int:pk>/tasks/<int:task_pk>/complete/",
        views.task_complete,
        name="task_complete",
    ),
    path(
        "projects/<int:pk>/tasks/<int:task_pk>/reopen/",
        views.task_reopen,
        name="task_reopen",
    ),
    path(
        "projects/<int:pk>/tasks/<int:task_pk>/delete/",
        views.task_delete,
        name="task_delete",
    ),
    path("projects/<int:pk>/sessions/new/", views.session_create, name="session_create"),
    path("projects/<int:pk>/sprints/new/", views.sprint_create, name="sprint_create"),
    path("sessions/<int:pk>/", views.session_manage, name="session_manage"),
    path("sessions/<int:pk>/start/", views.session_start, name="session_start"),
    path(
        "sessions/<int:pk>/navigate/<str:direction>/",
        views.session_navigate,
        name="session_navigate",
    ),
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
    path("sessions/<int:pk>/archive/", views.session_archive, name="session_archive"),
    path("sessions/<int:pk>/restore/", views.session_restore, name="session_restore"),
    path("sessions/<int:pk>/delete/", views.session_delete, name="session_delete"),
    path("sessions/<int:pk>/copy/", views.session_copy, name="session_copy"),
    path(
        "sessions/<int:pk>/participants/<int:participant_pk>/resume-link/rotate/",
        views.participant_resume_rotate,
        name="participant_resume_rotate",
    ),
    path("sessions/<int:pk>/state/", views.session_state, name="session_state"),
    path("room/<uuid:token>/", views.room, name="room"),
    path("room/<uuid:token>/join/", views.room_join, name="room_join"),
    path(
        "room/<uuid:token>/resume/<uuid:participant_token>/",
        views.room_resume,
        name="room_resume",
    ),
    path("room/<uuid:token>/state/", views.room_state, name="room_state"),
    path("room/<uuid:token>/vote/", views.room_vote, name="room_vote"),
    path(
        "room/<uuid:token>/navigate/",
        views.room_navigate,
        name="room_navigate",
    ),
    path(
        "room/<uuid:token>/complete/",
        views.room_complete,
        name="room_complete",
    ),
    path("sprints/<int:pk>/", views.sprint_detail, name="sprint_detail"),
    path(
        "sprints/<int:pk>/status/",
        views.sprint_set_status,
        name="sprint_set_status",
    ),
    path("sprints/<int:pk>/archive/", views.sprint_archive, name="sprint_archive"),
    path("sprints/<int:pk>/restore/", views.sprint_restore, name="sprint_restore"),
    path("sprints/<int:pk>/delete/", views.sprint_delete, name="sprint_delete"),
    path("sprints/<int:pk>/copy/", views.sprint_copy, name="sprint_copy"),
    path(
        "sprints/<int:pk>/transfer/",
        views.sprint_transfer_tasks,
        name="sprint_transfer_tasks",
    ),
    path("sprints/<int:pk>/tasks/add/", views.sprint_add_tasks, name="sprint_add_tasks"),
    path(
        "sprints/<int:pk>/tasks/<int:task_pk>/remove/",
        views.sprint_remove_task,
        name="sprint_remove_task",
    ),
    path("sprints/<int:pk>/export/", views.sprint_export, name="sprint_export"),
]
