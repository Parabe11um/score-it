from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .forms import ProjectMemberForm, SprintResourceForm, SprintSettingsForm
from .models import Project, ProjectMember, Sprint
from .team import add_project_members


@login_required
@require_http_methods(["GET", "POST"])
def project_team(request, pk, member_pk=None):
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    member = get_object_or_404(ProjectMember, pk=member_pk, project=project) if member_pk else None
    form = ProjectMemberForm(request.POST if request.method == "POST" else None, instance=member)
    if request.method == "POST" and form.is_valid():
        member = form.save(commit=False)
        member.project = project
        member.save()
        messages.success(request, "Сотрудник сохранён. Параметры существующих спринтов не изменились.")
        return redirect("poker:project_team", pk=project.pk)
    return render(request, "poker/project_team.html", {
        "project": project, "members": project.members.all(), "member": member, "member_form": form,
    }, status=400 if request.method == "POST" else 200)


def _owned_sprint(request, pk):
    query = Sprint.objects.select_related("project")
    if request.method == "POST":
        query = query.select_for_update()
    return get_object_or_404(query, pk=pk, project__owner=request.user)


def _team_url(sprint):
    return reverse("poker:sprint_team", args=[sprint.pk])


def _locked(sprint):
    return bool(sprint.archived_at or sprint.status == Sprint.Status.COMPLETED)


def _check_editable(request, sprint):
    if _locked(sprint):
        messages.error(request, "Команду и ёмкость завершённого или архивного спринта нельзя менять.")
        return redirect(_team_url(sprint))
    return None


def _render_team(request, sprint, settings_form=None, resource_form=None, status=200):
    resources = sprint.resource_rows
    forms = []
    for resource in resources:
        resource.sprint = sprint
        form = (resource_form if resource_form is not None and resource_form.instance.pk == resource.pk
                else SprintResourceForm(instance=resource, auto_id=f"resource_{resource.pk}_%s"))
        forms.append((resource, form))
    available = sprint.project.members.filter(is_active=True).exclude(
        pk__in=sprint.resources.filter(member__isnull=False).values("member_id")
    )
    return render(request, "poker/sprint_team.html", {
        "sprint": sprint, "resource_forms": forms, "available_members": available,
        "settings_form": settings_form or SprintSettingsForm(instance=sprint),
        "is_locked": _locked(sprint),
    }, status=status)


@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def sprint_team(request, pk):
    sprint = _owned_sprint(request, pk)
    if request.method == "POST":
        blocked = _check_editable(request, sprint)
        if blocked is not None:
            return blocked
        form = SprintSettingsForm(request.POST, instance=sprint)
        if form.is_valid():
            form.save()
            messages.success(request, "Календарь и способ расчёта ёмкости сохранены.")
            return redirect(_team_url(sprint))
        # The model form may have assigned invalid, unsaved inputs to its instance.
        sprint = _owned_sprint(request, pk)
        return _render_team(request, sprint, settings_form=form, status=400)
    return _render_team(request, sprint)


@login_required
@require_POST
@transaction.atomic
def sprint_members_add(request, pk):
    sprint = _owned_sprint(request, pk)
    blocked = _check_editable(request, sprint)
    if blocked is not None:
        return blocked
    members = sprint.project.members.filter(is_active=True)
    if request.POST.get("all") != "1":
        member = get_object_or_404(members, pk=request.POST.get("member_id") if request.POST.get("member_id", "").isdecimal() else None)
        members = [member]
    count = add_project_members(sprint, members)
    messages.success(request, f"Добавлено сотрудников: {count}. Уже сохранённые параметры не изменены.")
    return redirect(_team_url(sprint))


@login_required
@require_POST
@transaction.atomic
def sprint_resource_update(request, pk, resource_pk):
    sprint = _owned_sprint(request, pk)
    blocked = _check_editable(request, sprint)
    if blocked is not None:
        return blocked
    resource = get_object_or_404(sprint.resources.all(), pk=resource_pk)
    resource.sprint = sprint
    form = SprintResourceForm(request.POST, instance=resource, auto_id=f"resource_{resource.pk}_%s")
    if not form.is_valid():
        return _render_team(request, sprint, resource_form=form, status=400)
    form.save()
    messages.success(request, f"Параметры сотрудника «{resource.full_name}» в этом спринте сохранены.")
    return redirect(_team_url(sprint))


@login_required
@require_POST
@transaction.atomic
def sprint_resource_remove(request, pk, resource_pk):
    sprint = _owned_sprint(request, pk)
    blocked = _check_editable(request, sprint)
    if blocked is not None:
        return blocked
    resource = get_object_or_404(sprint.resources.all(), pk=resource_pk)
    resource.delete()
    messages.info(request, "Сотрудник исключён из расчёта этого спринта.")
    return redirect(_team_url(sprint))
