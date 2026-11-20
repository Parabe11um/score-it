from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from .forms import SprintForm, VotingSessionForm
from .models import Project, Sprint, Task, VotingSession, VotingSessionTask


def _owned_project(request, pk):
    return get_object_or_404(Project, pk=pk, owner=request.user)


def _work_list(request, project, items, *, section, heading, route, empty_message, form_class, form):
    archived = form is None and request.GET.get("archive") == "1"
    items = items.filter(archived_at__isnull=not archived).order_by("-created_at", "-pk")
    return render(
        request,
        "poker/workspace_list.html",
        {
            "project": project,
            "project_section": section,
            "heading": heading,
            "list_route": route,
            "empty_message": empty_message,
            "page": Paginator(items, 25).get_page(request.GET.get("page")),
            "archived": archived,
            "create_form": form if form is not None else (
                None if archived else form_class(project=project)
            ),
            "competency_choices": Task.Competency.choices,
        },
        status=400 if form is not None else 200,
    )


def project_voting_response(request, project, *, form=None):
    return _work_list(
        request, project, project.voting_sessions.all(),
        section="voting", heading="Голосования", route="poker:project_voting",
        empty_message="Создайте первое голосование, чтобы команда оценила задачи.",
        form_class=VotingSessionForm, form=form,
    )


def project_sprints_response(request, project, *, form=None):
    return _work_list(
        request, project, project.sprints.all(),
        section="sprints", heading="Спринты", route="poker:project_sprints",
        empty_message="Создайте первый спринт, чтобы запланировать оценённые задачи.",
        form_class=SprintForm, form=form,
    )


@login_required
@require_safe
def user_settings(request):
    return render(request, "poker/user_settings.html")


@login_required
@require_safe
def project_overview(request, pk):
    project = _owned_project(request, pk)
    active_rooms = project.voting_sessions.filter(
        status=VotingSession.Status.ACTIVE, archived_at__isnull=True,
    )
    active_sprints = project.sprints.filter(
        status=Sprint.Status.ACTIVE, archived_at__isnull=True,
    )
    return render(request, "poker/project_overview.html", {
        "project": project,
        "project_section": "overview",
        "unestimated_task_count": project.tasks.filter(
            completed_at__isnull=True, status=Task.Status.UNESTIMATED,
        ).count(),
        "active_room_count": active_rooms.count(),
        "active_sprint_count": active_sprints.count(),
        "active_rooms": active_rooms.annotate(
            queue_total=Count("queue_items"),
            queue_completed=Count(
                "queue_items",
                filter=Q(queue_items__status=VotingSessionTask.Status.COMPLETED),
            ),
        ).order_by("-created_at", "-pk")[:5],
        "active_sprints": active_sprints.order_by(
            F("end_date").asc(nulls_last=True), "-pk",
        )[:5],
        "today": timezone.localdate(),
    })


@login_required
@require_safe
def project_voting(request, pk):
    return project_voting_response(request, _owned_project(request, pk))


@login_required
@require_safe
def project_sprints(request, pk):
    return project_sprints_response(request, _owned_project(request, pk))


def _legacy_section(request, route, *, has_archive=False):
    project_ids = list(
        Project.objects.filter(owner=request.user).order_by("pk").values_list("pk", flat=True)[:2]
    )
    if len(project_ids) != 1:
        return redirect("poker:dashboard")
    location = reverse(route, args=project_ids)
    if has_archive and request.GET.get("archive") == "1":
        location += "?archive=1"
    return redirect(location)


@login_required
@require_safe
def voting_list(request):
    return _legacy_section(request, "poker:project_voting", has_archive=True)


@login_required
@require_safe
def sprint_list(request):
    return _legacy_section(request, "poker:project_sprints", has_archive=True)


@login_required
@require_safe
def team_list(request):
    return _legacy_section(request, "poker:project_team")
