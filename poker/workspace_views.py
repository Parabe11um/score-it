from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render
from django.views.decorators.http import require_safe

from .models import Project, Sprint, VotingSession


def _page(request, queryset):
    return Paginator(queryset, 25).get_page(request.GET.get("page"))


def _work_list(request, model, *, heading, description, route, empty_message, show_dates=False):
    archived = request.GET.get("archive") == "1"
    items = (
        model.objects.filter(project__owner=request.user, archived_at__isnull=not archived)
        .select_related("project")
        .order_by("-created_at", "-pk")
    )
    return render(
        request,
        "poker/workspace_list.html",
        {
            "heading": heading,
            "description": description,
            "list_route": route,
            "empty_message": empty_message,
            "page": _page(request, items),
            "archived": archived,
            "show_dates": show_dates,
        },
    )


@login_required
@require_safe
def user_settings(request):
    return render(request, "poker/user_settings.html")


@login_required
@require_safe
def voting_list(request):
    return _work_list(
        request,
        VotingSession,
        heading="Голосования",
        description="Комнаты оценки по всем вашим проектам.",
        route="poker:voting_list",
        empty_message="Голосований пока нет. Создайте комнату на странице проекта.",
    )


@login_required
@require_safe
def sprint_list(request):
    return _work_list(
        request,
        Sprint,
        heading="Спринты",
        description="Планы и сроки по всем вашим проектам.",
        route="poker:sprint_list",
        empty_message="Спринтов пока нет. Создайте спринт на странице проекта.",
        show_dates=True,
    )


@login_required
@require_safe
def team_list(request):
    projects = (
        Project.objects.filter(owner=request.user)
        .annotate(active_member_count=Count("members", filter=Q(members__is_active=True)))
        .order_by("name", "pk")
    )
    return render(request, "poker/team_list.html", {"page": _page(request, projects)})
