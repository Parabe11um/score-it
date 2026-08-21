from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    BulkTaskImportForm,
    JoinRoomForm,
    ProjectForm,
    SprintForm,
    VotingSessionForm,
)
from .models import (
    ESTIMATION_VALUES,
    Participant,
    Project,
    Sprint,
    SprintTask,
    Task,
    Vote,
    VotingRound,
    VotingSession,
    VotingSessionTask,
)


def _format_decimal(value):
    if value is None:
        return None
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded.normalize(), "f")


def _project_for_user(user, pk):
    return get_object_or_404(Project, pk=pk, owner=user)


def _session_for_user(user, pk):
    return get_object_or_404(
        VotingSession.objects.select_related("project", "current_task"),
        pk=pk,
        project__owner=user,
    )


def _sprint_for_user(user, pk):
    return get_object_or_404(
        Sprint.objects.select_related("project"), pk=pk, project__owner=user
    )


def _participant_session_key(voting_session):
    return f"score_it_participant_{voting_session.pk}"


def _participant_from_request(request, voting_session):
    token = request.session.get(_participant_session_key(voting_session))
    if not token:
        return None
    return Participant.objects.filter(
        session=voting_session, client_token=token
    ).first()


def _add_tasks_to_queue(voting_session, tasks):
    existing_ids = set(
        voting_session.queue_items.values_list("task_id", flat=True)
    )
    position = (
        voting_session.queue_items.aggregate(value=Max("position"))["value"] or 0
    )
    queue_items = []
    for task in tasks:
        if task.pk in existing_ids:
            continue
        position += 1
        queue_items.append(
            VotingSessionTask(
                session=voting_session,
                task=task,
                position=position,
            )
        )
    VotingSessionTask.objects.bulk_create(queue_items)
    return queue_items


def _create_round_for_item(voting_session, queue_item):
    last_number = (
        VotingRound.objects.filter(
            session=voting_session, task=queue_item.task
        ).aggregate(value=Max("number"))["value"]
        or 0
    )
    voting_round = VotingRound.objects.create(
        session=voting_session,
        task=queue_item.task,
        number=last_number + 1,
    )
    queue_item.status = VotingSessionTask.Status.ACTIVE
    queue_item.completed_at = None
    queue_item.current_round = voting_round
    queue_item.save(
        update_fields=("status", "completed_at", "current_round")
    )
    return voting_round


def _open_pending_queue_items(voting_session):
    queue_items = list(
        voting_session.queue_items.select_related("task").filter(
            status__in=(
                VotingSessionTask.Status.PENDING,
                VotingSessionTask.Status.SKIPPED,
            )
        )
    )
    for queue_item in queue_items:
        _create_round_for_item(voting_session, queue_item)

    first_item = voting_session.queue_items.select_related("task").filter(
        status=VotingSessionTask.Status.ACTIVE
    ).first()
    if first_item:
        current_is_open = voting_session.queue_items.filter(
            task_id=voting_session.current_task_id,
            status=VotingSessionTask.Status.ACTIVE,
        ).exists()
        if not current_is_open:
            voting_session.current_task = first_item.task
        voting_session.status = VotingSession.Status.ACTIVE
        voting_session.save(update_fields=("current_task", "status"))
    return queue_items


def _focus_next_open_item(voting_session, current_position):
    next_item = (
        voting_session.queue_items.select_related("task")
        .filter(
            position__gt=current_position,
            status=VotingSessionTask.Status.ACTIVE,
        )
        .first()
    )
    if next_item is None:
        next_item = (
            voting_session.queue_items.select_related("task")
            .filter(status=VotingSessionTask.Status.ACTIVE)
            .first()
        )
    voting_session.current_task = next_item.task if next_item else None
    voting_session.save(update_fields=("current_task",))
    return next_item


def _queue_context(voting_session):
    queue_items = list(
        voting_session.queue_items.select_related("task", "current_round").annotate(
            vote_count=Count("current_round__votes")
        )
    )
    completed = sum(
        item.status == VotingSessionTask.Status.COMPLETED for item in queue_items
    )
    pending = sum(
        item.status == VotingSessionTask.Status.PENDING for item in queue_items
    )
    active = sum(
        item.status == VotingSessionTask.Status.ACTIVE for item in queue_items
    )
    skipped = sum(
        item.status == VotingSessionTask.Status.SKIPPED for item in queue_items
    )
    current_position = next(
        (
            index
            for index, item in enumerate(queue_items, start=1)
            if item.task_id == voting_session.current_task_id
        ),
        None,
    )
    for index, item in enumerate(queue_items, start=1):
        item.display_position = index
        item.is_current = item.task_id == voting_session.current_task_id
    focusable_items = [
        item for item in queue_items if item.status == VotingSessionTask.Status.ACTIVE
    ]
    focus_index = next(
        (
            index
            for index, item in enumerate(focusable_items)
            if item.task_id == voting_session.current_task_id
        ),
        None,
    )
    return {
        "items": queue_items,
        "total": len(queue_items),
        "completed": completed,
        "pending": pending,
        "active": active,
        "skipped": skipped,
        "current_position": current_position,
        "has_previous": focus_index is not None and focus_index > 0,
        "has_next": focus_index is not None
        and focus_index < len(focusable_items) - 1,
    }


def _queue_summary(voting_session):
    summary = voting_session.queue_items.aggregate(
        total=Count("id"),
        completed=Count(
            "id", filter=Q(status=VotingSessionTask.Status.COMPLETED)
        ),
        pending=Count("id", filter=Q(status=VotingSessionTask.Status.PENDING)),
        active=Count("id", filter=Q(status=VotingSessionTask.Status.ACTIVE)),
        skipped=Count("id", filter=Q(status=VotingSessionTask.Status.SKIPPED)),
    )
    summary["current_position"] = (
        voting_session.queue_items.filter(task_id=voting_session.current_task_id)
        .values_list("position", flat=True)
        .first()
    )
    return summary


def _participant_queue_item(voting_session, participant):
    queue_items = voting_session.queue_items.select_related("task", "current_round")
    queue_item = None
    if participant.current_task_id:
        queue_item = queue_items.filter(task_id=participant.current_task_id).first()
    if queue_item is None:
        queue_item = queue_items.filter(
            status=VotingSessionTask.Status.ACTIVE
        ).first() or queue_items.first()
        if queue_item:
            participant.current_task = queue_item.task
            participant.save(update_fields=("current_task",))
    return queue_item


def _participant_queue_summary(voting_session, participant, queue_item):
    summary = voting_session.queue_items.aggregate(
        total=Count("id", distinct=True),
        completed=Count(
            "id",
            filter=Q(status=VotingSessionTask.Status.COMPLETED),
            distinct=True,
        ),
        voted=Count(
            "current_round__votes",
            filter=Q(current_round__votes__participant=participant),
            distinct=True,
        ),
    )
    position = queue_item.position if queue_item else None
    summary.update(
        {
            "current_position": position,
            "has_previous": bool(position and position > 1),
            "has_next": bool(position and position < summary["total"]),
        }
    )
    return summary


@login_required
def dashboard(request):
    projects = (
        Project.objects.filter(owner=request.user)
        .prefetch_related("tasks", "voting_sessions", "sprints")
        .order_by("-updated_at")
    )
    return render(
        request,
        "poker/dashboard.html",
        {"projects": projects, "project_form": ProjectForm()},
    )


@login_required
@require_POST
def project_create(request):
    form = ProjectForm(request.POST)
    if form.is_valid():
        project = form.save(commit=False)
        project.owner = request.user
        project.save()
        messages.success(request, f"Проект «{project.name}» создан.")
        return redirect(project)

    projects = Project.objects.filter(owner=request.user)
    return render(
        request,
        "poker/dashboard.html",
        {"projects": projects, "project_form": form},
        status=400,
    )


@login_required
def project_detail(request, pk):
    project = _project_for_user(request.user, pk)
    tasks = project.tasks.all()
    sessions = project.voting_sessions.select_related("current_task")
    sprints = project.sprints.all()
    return render(
        request,
        "poker/project_detail.html",
        {
            "project": project,
            "tasks": tasks,
            "sessions": sessions,
            "sprints": sprints,
            "task_import_form": BulkTaskImportForm(),
            "session_form": VotingSessionForm(project=project),
            "sprint_form": SprintForm(),
        },
    )


@login_required
@require_POST
def task_import(request, pk):
    project = _project_for_user(request.user, pk)
    form = BulkTaskImportForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Проверьте формат списка задач.")
        return render(
            request,
            "poker/project_detail.html",
            {
                "project": project,
                "tasks": project.tasks.all(),
                "sessions": project.voting_sessions.all(),
                "sprints": project.sprints.all(),
                "task_import_form": form,
                "session_form": VotingSessionForm(project=project),
                "sprint_form": SprintForm(),
            },
            status=400,
        )

    created_count = 0
    updated_count = 0
    with transaction.atomic():
        for number, title in form.parsed_tasks:
            task, created = Task.objects.get_or_create(
                project=project, number=number, defaults={"title": title}
            )
            if created:
                created_count += 1
            elif task.title != title:
                task.title = title
                task.save(update_fields=("title", "updated_at"))
                updated_count += 1

    messages.success(
        request,
        f"Импорт завершён: добавлено {created_count}, обновлено {updated_count}.",
    )
    return redirect(project)


@login_required
@require_POST
def session_create(request, pk):
    project = _project_for_user(request.user, pk)
    form = VotingSessionForm(request.POST, project=project)
    if form.is_valid():
        with transaction.atomic():
            voting_session = form.save(commit=False)
            voting_session.project = project
            voting_session.save()
            queued_items = _add_tasks_to_queue(
                voting_session,
                project.tasks.filter(pk__in=form.cleaned_data["task_ids"]),
            )
        messages.success(
            request,
            f"Комната создана. В очередь добавлено задач: {len(queued_items)}.",
        )
        return redirect(voting_session)
    messages.error(request, "Не удалось создать комнату: проверьте параметры.")
    return render(
        request,
        "poker/project_detail.html",
        {
            "project": project,
            "tasks": project.tasks.all(),
            "sessions": project.voting_sessions.select_related("current_task"),
            "sprints": project.sprints.all(),
            "task_import_form": BulkTaskImportForm(),
            "session_form": form,
            "sprint_form": SprintForm(),
        },
        status=400,
    )


@login_required
def session_manage(request, pk):
    voting_session = _session_for_user(request.user, pk)
    current_round = voting_session.current_round
    queue = _queue_context(voting_session)
    queued_task_ids = [item.task_id for item in queue["items"]]
    available_tasks = voting_session.project.tasks.exclude(pk__in=queued_task_ids)
    public_url = request.build_absolute_uri(voting_session.get_public_url())
    summary = current_round.summary() if current_round else None
    return render(
        request,
        "poker/session_manage.html",
        {
            "voting_session": voting_session,
            "queue": queue,
            "available_tasks": available_tasks,
            "current_round": current_round,
            "summary": summary,
            "average_display": _format_decimal(summary["average"])
            if summary
            else None,
            "public_url": public_url,
        },
    )


@login_required
@require_POST
def session_queue_add(request, pk):
    voting_session = _session_for_user(request.user, pk)
    if voting_session.status == VotingSession.Status.FINISHED:
        messages.error(request, "В завершённую сессию нельзя добавлять задачи.")
        return redirect(voting_session)

    task_ids = request.POST.getlist("task_ids")
    tasks = voting_session.project.tasks.filter(pk__in=task_ids)
    with transaction.atomic():
        created_items = _add_tasks_to_queue(voting_session, tasks)
        if voting_session.status == VotingSession.Status.ACTIVE:
            _open_pending_queue_items(voting_session)
    created = len(created_items)
    if created:
        messages.success(request, f"В очередь добавлено задач: {created}.")
    else:
        messages.info(request, "Выберите хотя бы одну новую задачу.")
    return redirect(voting_session)


@login_required
@require_POST
def session_start(request, pk):
    voting_session = _session_for_user(request.user, pk)
    if voting_session.status == VotingSession.Status.FINISHED:
        messages.error(request, "Завершённую сессию нельзя продолжить.")
        return redirect(voting_session)
    if voting_session.status == VotingSession.Status.ACTIVE:
        messages.info(request, "Голосование уже открыто для всех задач очереди.")
        return redirect(voting_session)

    with transaction.atomic():
        opened_items = _open_pending_queue_items(voting_session)
    if not opened_items and not voting_session.queue_items.filter(
        status=VotingSessionTask.Status.ACTIVE
    ).exists():
        messages.info(request, "В очереди нет задач, ожидающих оценки.")
    else:
        messages.success(
            request,
            "Асинхронное голосование открыто для всех задач очереди.",
        )
    return redirect(voting_session)


@login_required
@require_POST
def session_navigate(request, pk, direction):
    voting_session = _session_for_user(request.user, pk)
    if direction not in ("previous", "next"):
        messages.error(request, "Неизвестное направление перехода.")
        return redirect(voting_session)

    queue_items = list(
        voting_session.queue_items.select_related("task").filter(
            status=VotingSessionTask.Status.ACTIVE
        )
    )
    current_index = next(
        (
            index
            for index, item in enumerate(queue_items)
            if item.task_id == voting_session.current_task_id
        ),
        None,
    )
    if current_index is None:
        target = queue_items[0] if queue_items else None
    else:
        target_index = current_index + (1 if direction == "next" else -1)
        target = (
            queue_items[target_index]
            if 0 <= target_index < len(queue_items)
            else None
        )
    if target is None:
        messages.info(request, "В этом направлении больше нет открытых задач.")
        return redirect(voting_session)

    voting_session.current_task = target.task
    voting_session.save(update_fields=("current_task",))
    return redirect(voting_session)


@login_required
@require_POST
def session_start_task(request, pk, task_pk):
    voting_session = _session_for_user(request.user, pk)
    task = get_object_or_404(Task, pk=task_pk, project=voting_session.project)

    if voting_session.status == VotingSession.Status.FINISHED:
        messages.error(request, "Завершённую сессию нельзя продолжить.")
        return redirect(voting_session)
    with transaction.atomic():
        queue_item = voting_session.queue_items.filter(task=task).first()
        if queue_item is None:
            _add_tasks_to_queue(voting_session, [task])
            queue_item = voting_session.queue_items.get(task=task)
        if queue_item.current_round_id is None or queue_item.current_round.status in (
            VotingRound.Status.CLOSED,
            VotingRound.Status.CANCELLED,
        ):
            _create_round_for_item(voting_session, queue_item)
        voting_session.current_task = task
        voting_session.status = VotingSession.Status.ACTIVE
        voting_session.save(update_fields=("current_task", "status"))

    messages.success(request, f"Открыто голосование по задаче {task.number}.")
    return redirect(voting_session)


@login_required
@require_POST
def session_reveal(request, pk):
    voting_session = _session_for_user(request.user, pk)
    voting_round = voting_session.current_round
    if not voting_round or voting_round.status != VotingRound.Status.VOTING:
        messages.error(request, "Нет активного голосования для раскрытия.")
        return redirect(voting_session)
    vote_count = voting_round.votes.count()
    if vote_count < voting_session.minimum_participants:
        remaining = voting_session.minimum_participants - vote_count
        messages.error(
            request,
            f"Для раскрытия не хватает голосов: нужно ещё {remaining}.",
        )
        return redirect(voting_session)

    voting_round.status = VotingRound.Status.REVEALED
    voting_round.revealed_at = timezone.now()
    voting_round.save(update_fields=("status", "revealed_at"))
    return redirect(voting_session)


@login_required
@require_POST
def session_revote(request, pk):
    voting_session = _session_for_user(request.user, pk)
    voting_round = voting_session.current_round
    if not voting_round or voting_round.status != VotingRound.Status.REVEALED:
        messages.error(request, "Повторное голосование сейчас недоступно.")
        return redirect(voting_session)

    with transaction.atomic():
        voting_round.status = VotingRound.Status.CANCELLED
        voting_round.save(update_fields=("status",))
        queue_item = voting_session.queue_items.get(task=voting_round.task)
        new_round = VotingRound.objects.create(
            session=voting_session,
            task=voting_round.task,
            number=voting_round.number + 1,
        )
        queue_item.current_round = new_round
        queue_item.status = VotingSessionTask.Status.ACTIVE
        queue_item.completed_at = None
        queue_item.save(
            update_fields=("current_round", "status", "completed_at")
        )
    messages.info(request, "Начат новый раунд. Предыдущие голоса сохранены в истории.")
    return redirect(voting_session)


@login_required
@require_POST
def session_accept(request, pk):
    voting_session = _session_for_user(request.user, pk)
    voting_round = voting_session.current_round
    if not voting_round or voting_round.status != VotingRound.Status.REVEALED:
        messages.error(request, "Сначала раскройте голоса.")
        return redirect(voting_session)

    try:
        with transaction.atomic():
            queue_item = voting_session.queue_items.get(task=voting_round.task)
            voting_round.task.capture_estimate(voting_round)
            voting_round.status = VotingRound.Status.CLOSED
            voting_round.closed_at = timezone.now()
            voting_round.save(update_fields=("status", "closed_at"))
            queue_item.status = VotingSessionTask.Status.COMPLETED
            queue_item.completed_at = timezone.now()
            queue_item.save(update_fields=("status", "completed_at"))
            next_item = _focus_next_open_item(voting_session, queue_item.position)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(voting_session)

    messages.success(
        request,
        f"Оценка задачи {voting_round.task.number} сохранена: "
        f"{voting_round.task.estimate_display}."
        + (
            f" Следующая задача для проверки: {next_item.task.number}."
            if next_item
            else " Очередь завершена."
        ),
    )
    return redirect(voting_session)


@login_required
@require_POST
def session_finish(request, pk):
    voting_session = _session_for_user(request.user, pk)
    with transaction.atomic():
        voting_session.rounds.filter(
            status__in=(VotingRound.Status.VOTING, VotingRound.Status.REVEALED)
        ).update(status=VotingRound.Status.CANCELLED)
        voting_session.queue_items.filter(
            status=VotingSessionTask.Status.ACTIVE
        ).update(status=VotingSessionTask.Status.SKIPPED)
        voting_session.current_task = None
        voting_session.status = VotingSession.Status.FINISHED
        voting_session.finished_at = timezone.now()
        voting_session.save(
            update_fields=("current_task", "status", "finished_at")
        )
    messages.success(request, "Сессия голосования завершена.")
    return redirect(voting_session)


@login_required
@require_GET
def session_state(request, pk):
    voting_session = _session_for_user(request.user, pk)
    voting_round = voting_session.current_round
    voted_ids = set()
    votes = []
    summary = None

    if voting_round:
        round_votes = list(
            voting_round.votes.select_related("participant").order_by(
                "participant__joined_at"
            )
        )
        voted_ids = {vote.participant_id for vote in round_votes}
        if voting_round.status == VotingRound.Status.REVEALED:
            votes = [
                {"name": vote.participant.name, "value": vote.value}
                for vote in round_votes
            ]
            round_summary = voting_round.summary()
            summary = {
                "count": round_summary["count"],
                "sum": round_summary["sum"],
                "average": _format_decimal(round_summary["average"]),
            }

    participants = [
        {"name": participant.name, "voted": participant.pk in voted_ids}
        for participant in voting_session.participants.all()
    ]
    queue = _queue_summary(voting_session)
    queue_items = list(
        voting_session.queue_items.annotate(
            vote_count=Count("current_round__votes")
        ).values("id", "status", "vote_count")
    )
    minimum_reached = len(voted_ids) >= voting_session.minimum_participants
    return JsonResponse(
        {
            "session_status": voting_session.status,
            "current_task": {
                "number": voting_session.current_task.number,
                "title": voting_session.current_task.title,
            }
            if voting_session.current_task
            else None,
            "round_status": voting_round.status if voting_round else None,
            "participants": participants,
            "voted_count": len(voted_ids),
            "participant_count": len(participants),
            "minimum_participants": voting_session.minimum_participants,
            "minimum_reached": minimum_reached,
            "votes_remaining": max(
                voting_session.minimum_participants - len(voted_ids), 0
            ),
            "queue": {
                "total": queue["total"],
                "completed": queue["completed"],
                "pending": queue["pending"],
                "current_position": queue["current_position"],
            },
            "queue_items": queue_items,
            "votes": votes,
            "summary": summary,
        }
    )


def room(request, token):
    voting_session = get_object_or_404(
        VotingSession.objects.select_related("project", "current_task"),
        public_token=token,
    )
    participant = _participant_from_request(request, voting_session)
    if not participant:
        return render(
            request,
            "poker/room_join.html",
            {"voting_session": voting_session, "join_form": JoinRoomForm()},
        )
    if participant.completed_at:
        return render(
            request,
            "poker/room_completed.html",
            {"voting_session": voting_session},
        )
    return render(
        request,
        "poker/room.html",
        {
            "voting_session": voting_session,
            "participant": participant,
            "estimation_values": ESTIMATION_VALUES,
        },
    )


@require_POST
def room_join(request, token):
    voting_session = get_object_or_404(VotingSession, public_token=token)
    if voting_session.status == VotingSession.Status.FINISHED:
        messages.error(request, "Эта сессия уже завершена.")
        return redirect("poker:room", token=token)

    form = JoinRoomForm(request.POST)
    if form.is_valid():
        name = form.cleaned_data["name"].strip()
        existing_names = voting_session.participants.values_list("name", flat=True)
        if any(existing.casefold() == name.casefold() for existing in existing_names):
            form.add_error("name", "Это имя уже используется в комнате.")
        else:
            first_task_id = (
                voting_session.queue_items.filter(
                    status=VotingSessionTask.Status.ACTIVE
                )
                .values_list("task_id", flat=True)
                .first()
                or voting_session.queue_items.values_list(
                    "task_id", flat=True
                ).first()
            )
            participant = Participant.objects.create(
                session=voting_session, name=name, current_task_id=first_task_id
            )
            request.session[_participant_session_key(voting_session)] = str(
                participant.client_token
            )
            request.session.modified = True
            return redirect("poker:room", token=token)

    return render(
        request,
        "poker/room_join.html",
        {"voting_session": voting_session, "join_form": form},
        status=400,
    )


@require_GET
def room_state(request, token):
    voting_session = get_object_or_404(
        VotingSession.objects.select_related("current_task"), public_token=token
    )
    participant = _participant_from_request(request, voting_session)
    if not participant:
        return JsonResponse({"error": "participant_required"}, status=403)

    Participant.objects.filter(pk=participant.pk).update(last_seen_at=timezone.now())
    queue_item = _participant_queue_item(voting_session, participant)
    queue = _participant_queue_summary(voting_session, participant, queue_item)
    response = {
        "session_status": voting_session.status,
        "session_name": voting_session.name,
        "participant_completed": participant.completed_at is not None,
        "current_task": None,
        "round": None,
        "minimum_participants": voting_session.minimum_participants,
        "queue": queue,
    }
    if (
        voting_session.status == VotingSession.Status.DRAFT
        or queue_item is None
        or queue_item.current_round_id is None
    ):
        return JsonResponse(response)

    voting_round = queue_item.current_round
    vote = voting_round.votes.filter(participant=participant).first()
    vote_count = voting_round.votes.count()
    round_data = {
        "status": voting_round.status,
        "number": voting_round.number,
        "has_voted": vote is not None,
        "my_vote": vote.value if vote else None,
        "voted_count": vote_count,
        "participant_count": voting_session.participants.count(),
        "minimum_participants": voting_session.minimum_participants,
        "minimum_reached": vote_count >= voting_session.minimum_participants,
        "votes": [],
        "average": None,
    }
    if voting_round.status in (
        VotingRound.Status.REVEALED,
        VotingRound.Status.CLOSED,
    ):
        revealed_votes = voting_round.votes.select_related("participant").order_by(
            "participant__joined_at"
        )
        round_data["votes"] = [
            {"name": item.participant.name, "value": item.value}
            for item in revealed_votes
        ]
        round_data["average"] = _format_decimal(
            voting_round.summary()["average"]
        )

    response["current_task"] = {
        "id": queue_item.task_id,
        "number": queue_item.task.number,
        "title": queue_item.task.title,
    }
    response["round"] = round_data
    return JsonResponse(response)


@require_POST
def room_vote(request, token):
    voting_session = get_object_or_404(VotingSession, public_token=token)
    participant = _participant_from_request(request, voting_session)
    if not participant:
        return JsonResponse({"error": "participant_required"}, status=403)
    if participant.completed_at:
        return JsonResponse({"error": "participant_completed"}, status=409)
    queue_item = _participant_queue_item(voting_session, participant)
    voting_round = queue_item.current_round if queue_item else None
    if not voting_round or voting_round.status != VotingRound.Status.VOTING:
        return JsonResponse({"error": "voting_closed"}, status=409)

    try:
        value = int(request.POST.get("value", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "invalid_value"}, status=400)
    if value not in ESTIMATION_VALUES:
        return JsonResponse({"error": "invalid_value"}, status=400)

    Vote.objects.update_or_create(
        voting_round=voting_round,
        participant=participant,
        defaults={"value": value},
    )
    return JsonResponse({"ok": True, "value": value})


@require_POST
def room_navigate(request, token):
    voting_session = get_object_or_404(VotingSession, public_token=token)
    participant = _participant_from_request(request, voting_session)
    if not participant:
        return JsonResponse({"error": "participant_required"}, status=403)
    if participant.completed_at:
        return JsonResponse({"error": "participant_completed"}, status=409)

    direction = request.POST.get("direction")
    if direction not in ("previous", "next"):
        return JsonResponse({"error": "invalid_direction"}, status=400)

    queue_item = _participant_queue_item(voting_session, participant)
    if queue_item is None:
        return JsonResponse({"error": "empty_queue"}, status=409)

    position_filter = (
        {"position__gt": queue_item.position}
        if direction == "next"
        else {"position__lt": queue_item.position}
    )
    target_items = voting_session.queue_items.select_related("task").filter(
        **position_filter
    )
    target = target_items.first() if direction == "next" else target_items.last()
    if target is None:
        return JsonResponse({"error": "queue_boundary"}, status=409)

    participant.current_task = target.task
    participant.save(update_fields=("current_task",))
    return JsonResponse(
        {"ok": True, "position": target.position, "task_id": target.task_id}
    )


@require_POST
def room_complete(request, token):
    voting_session = get_object_or_404(VotingSession, public_token=token)
    participant = _participant_from_request(request, voting_session)
    if not participant:
        return JsonResponse({"error": "participant_required"}, status=403)
    if participant.completed_at:
        return JsonResponse({"ok": True})

    queue_item = _participant_queue_item(voting_session, participant)
    if queue_item is None:
        return JsonResponse({"error": "empty_queue"}, status=409)
    if (
        voting_session.status != VotingSession.Status.ACTIVE
        or queue_item.current_round_id is None
    ):
        return JsonResponse({"error": "voting_not_started"}, status=409)
    if voting_session.queue_items.filter(
        position__gt=queue_item.position
    ).exists():
        return JsonResponse({"error": "last_task_required"}, status=409)

    participant.completed_at = timezone.now()
    participant.save(update_fields=("completed_at",))
    return JsonResponse({"ok": True})


@login_required
@require_POST
def sprint_create(request, pk):
    project = _project_for_user(request.user, pk)
    form = SprintForm(request.POST)
    if form.is_valid():
        sprint = form.save(commit=False)
        sprint.project = project
        sprint.save()
        messages.success(request, f"Спринт «{sprint.name}» создан.")
        return redirect(sprint)
    messages.error(request, "Не удалось создать спринт: проверьте параметры.")
    return redirect(project)


@login_required
def sprint_detail(request, pk):
    sprint = _sprint_for_user(request.user, pk)
    sprint_items = sprint.sprint_tasks.select_related("task")
    available_tasks = sprint.project.tasks.filter(
        status=Task.Status.ESTIMATED, sprint_items__isnull=True
    )
    return render(
        request,
        "poker/sprint_detail.html",
        {
            "sprint": sprint,
            "sprint_items": sprint_items,
            "available_tasks": available_tasks,
        },
    )


@login_required
@require_POST
def sprint_add_tasks(request, pk):
    sprint = _sprint_for_user(request.user, pk)
    task_ids = request.POST.getlist("task_ids")
    tasks = sprint.project.tasks.filter(
        pk__in=task_ids, status=Task.Status.ESTIMATED, sprint_items__isnull=True
    )
    position = (
        sprint.sprint_tasks.aggregate(value=Max("position"))["value"] or 0
    )
    created = 0
    with transaction.atomic():
        for task in tasks:
            position += 1
            SprintTask.objects.create(sprint=sprint, task=task, position=position)
            created += 1
    messages.success(request, f"В спринт добавлено задач: {created}.")
    return redirect(sprint)


@login_required
@require_POST
def sprint_remove_task(request, pk, task_pk):
    sprint = _sprint_for_user(request.user, pk)
    item = get_object_or_404(SprintTask, sprint=sprint, task_id=task_pk)
    item.delete()
    messages.info(request, "Задача удалена из спринта.")
    return redirect(sprint)


@login_required
def sprint_export(request, pk):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    sprint = _sprint_for_user(request.user, pk)
    items = list(sprint.sprint_tasks.select_related("task"))

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Задачи спринта"
    headers = (
        "№",
        "Номер задачи",
        "Название",
        "Средняя оценка",
        "Сумма голосов",
        "Количество голосов",
    )
    sheet.append(headers)

    header_fill = PatternFill("solid", fgColor="243B53")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for row_number, item in enumerate(items, start=2):
        task = item.task
        sheet.append(
            (
                row_number - 1,
                task.number,
                task.title,
                f"=E{row_number}/F{row_number}" if task.estimate_count else None,
                task.estimate_sum,
                task.estimate_count,
            )
        )
        sheet.cell(row=row_number, column=4).number_format = "0.00"

    if items:
        total_row = len(items) + 2
        sheet.cell(row=total_row, column=3, value="Итого")
        sheet.cell(row=total_row, column=3).font = Font(bold=True)
        sheet.cell(row=total_row, column=4, value=f"=SUM(D2:D{total_row - 1})")
        sheet.cell(row=total_row, column=4).font = Font(bold=True)
        sheet.cell(row=total_row, column=4).number_format = "0.00"

    widths = (6, 20, 70, 20, 18, 22)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:F{max(1, len(items) + 1)}"

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    safe_name = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in sprint.name
    )
    response["Content-Disposition"] = (
        f'attachment; filename="sprint_{safe_name or sprint.pk}.xlsx"'
    )
    workbook.save(response)
    return response
