"""Manually reconcile an EVA export with project tasks and sprint membership."""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from .models import Project, Sprint, SprintTask, Task, VotingRound
from .task_import import (
    COMPETENCIES, ImportedTask, _column_mapping, _normalise, _text,
    read_task_file_rows, task_from_row,
)

CLOSED_STATUSES = {
    "закрыта", "закрыто", "закрыт", "выполнена", "выполнено", "выполнен",
    "завершена", "завершено", "завершен", "отменена", "отменено", "отменен",
    "closed", "done", "resolved", "cancelled", "canceled",
}
EMPTY_SPRINTS = {"", "нет", "—", "-", "[]"}


@dataclass(frozen=True)
class PlanningTask:
    task: ImportedTask
    estimate: Decimal | None
    eva_status: str
    eva_sprints: str
    skip_reason: str = ""


@dataclass
class PlanningImport:
    rows: list[PlanningTask] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    has_sprints_column: bool = False
    has_quota_column: bool = False
    has_status_column: bool = False
    relationship_columns: tuple[str, ...] = ()
    issues: list[str] = field(default_factory=list)

    @property
    def tasks(self):
        return [item.task for item in self.rows]

    @property
    def total_rows(self):
        return self.counts["total"]

    @property
    def duplicates(self):
        return self.counts["duplicates"]


def parse_sprint_file(upload, *, require_status=True):
    rows = read_task_file_rows(upload)
    columns = _column_mapping(rows[0])
    if require_status and "eva_status" not in columns:
        raise ValidationError("Не найдена колонка «Статус.Имя статуса». Выгрузите из EVA все поля.")
    result = PlanningImport(has_sprints_column="eva_sprints" in columns,
                            has_quota_column="quota" in columns,
                            has_status_column="eva_status" in columns,
                            relationship_columns=tuple(key for key in (
                                "eva_parent_title", "eva_project_name", "eva_epic_title"
                            ) if key in columns))
    seen, identifiers, errors = {}, {}, []
    for line, row in enumerate(rows[1:], 2):
        if not any(_text(cell) for cell in row):
            continue
        result.counts["total"] += 1

        def value(key):
            i = columns.get(key)
            return _text(row[i]) if i is not None and i < len(row) else ""

        if _normalise(value("competency")) not in COMPETENCIES:
            result.counts["other_type"] += 1
            result.issues.append(f"Строка {line}, {value('number')}: неизвестный тип «{value('competency')[:80]}», строка пропущена.")
            continue
        status, sprints = value("eva_status"), value("eva_sprints")
        if (require_status and not status) or len(status) > 200 or len(sprints) > 5000:
            errors.append(f"Строка {line}: проверьте статус задачи и значение спринта EVA.")
            continue
        if _normalise(sprints) in EMPTY_SPRINTS:
            sprints = ""
        raw = value("estimate")
        estimate, reason = None, "unestimated"
        if raw:
            try:
                numeric = Decimal(raw.replace(",", "."))
            except InvalidOperation:
                numeric = None
            if numeric is None or not numeric.is_finite() or numeric < 0 or numeric > Decimal("999999.99") or numeric != numeric.quantize(Decimal("0.01")):
                reason = "invalid_estimate"
                result.issues.append(f"Строка {line}, {value('number')}: оценка «{raw[:40]}» некорректна; строка пропущена. Нужно число от 0 до 999999,99, не более двух знаков после запятой.")
            elif numeric:
                estimate, reason = numeric, ""
            else:
                estimate = Decimal("0")
                result.counts["zero"] += 1
        if reason != "invalid_estimate" and (_normalise(status) in CLOSED_STATUSES or _normalise(value("eva_status_type")) in CLOSED_STATUSES):
            reason = "closed"
        try:
            task = task_from_row(row, columns, line)
        except ValidationError as exc:
            errors.extend(exc.messages)
            continue
        item = PlanningTask(task, estimate, status, sprints, reason)
        if task.number in seen:
            if seen[task.number] != item:
                errors.append(f"Строка {line}: код {task.number} повторяется с разными данными.")
            else:
                result.counts["duplicates"] += 1
            continue
        if task.external_url in identifiers and identifiers[task.external_url] != task.number:
            errors.append(f"Строка {line}: один идентификатор EVA указан у разных кодов задач.")
            continue
        identifiers[task.external_url] = task.number
        seen[task.number] = item
        if reason:
            result.counts[reason] += 1
    if errors:
        raise ValidationError(errors[:20])
    result.rows = list(seen.values())
    return result


def available_sprint_tasks(project):
    return (project.tasks.filter(status=Task.Status.ESTIMATED, completed_at__isnull=True, eva_block_reason="")
            .filter(Q(imported_estimate__isnull=False) | Q(estimate_sum__isnull=False, estimate_count__gt=0))
            .exclude(sprint_items__status=SprintTask.Status.PLANNED).distinct())


@dataclass
class SavedPlanningImport:
    counts: Counter = field(default_factory=Counter)
    issues: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)


def sprint_name(value):
    """Do not guess which sprint is current in an ambiguous multi-value export."""
    if not value:
        return ""
    if value.startswith("{"):
        raise ValueError("вместо названия спринта указан объект; выгрузите название")
    if value.startswith("["):
        try:
            names = json.loads(value)
        except (TypeError, ValueError):
            raise ValueError("не удалось прочитать список спринтов")
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError("ожидается список названий спринтов")
    else:
        names = re.split(r"[;,\r\n]+", value)
    names = list(dict.fromkeys(name.strip() for name in names if name.strip()))
    if len(names) != 1 or len(names[0]) > 160:
        raise ValueError("нужно одно однозначное название спринта длиной до 160 символов")
    return names[0]


@transaction.atomic
def save_sprint_import(project, parsed):
    Project.objects.select_for_update().get(pk=project.pk)
    result = SavedPlanningImport()
    for item in parsed.rows:
        task = project.tasks.select_for_update().filter(number=item.task.number).first()

        def mark_readiness_stale():
            # Preserve protected task/estimate fields, but don't trust stale readiness.
            if task and not task.eva_readiness_stale:
                task.eva_readiness_stale = True
                task.save(update_fields=("eva_readiness_stale",))

        def conflict(message):
            mark_readiness_stale()
            result.counts["conflicts"] += 1
            result.issues.append(f"{item.task.number}: {message}; данные строки не перезаписаны, готовность к оценке требует повторного импорта.")

        if item.skip_reason == "invalid_estimate":
            mark_readiness_stale()
            continue
        if task and task.eva_identifier and task.eva_identifier != item.task.external_url.split("?popup=", 1)[1]:
            conflict("отличается идентификатор EVA")
            continue
        if project.tasks.filter(external_url=item.task.external_url).exclude(number=item.task.number).exists():
            conflict("идентификатор EVA уже связан с другим кодом")
            continue
        if task and task.voting_rounds.filter(status__in=(VotingRound.Status.VOTING, VotingRound.Status.REVEALED)).exists():
            mark_readiness_stale()
            result.counts["voting"] += 1
            result.issues.append(f"{task.number}: идёт голосование; повторите импорт после его завершения.")
            continue
        current = list(task.sprint_items.filter(status=SprintTask.Status.PLANNED).select_related("sprint")) if task else []
        # Completed sprint totals depend on Task.estimate: don't mutate historical plans.
        if any(link.sprint.archived_at or link.sprint.status == Sprint.Status.COMPLETED for link in current):
            conflict("задача находится в завершённом или архивном спринте")
            continue
        target, name = None, ""
        if parsed.has_sprints_column:
            try:
                name = sprint_name(item.eva_sprints)
            except ValueError as exc:
                conflict(str(exc))
                continue
            if name:
                candidates = list(project.sprints.filter(name__iexact=name))
                if len(candidates) > 1:
                    conflict(f"несколько спринтов с названием «{name}»")
                    continue
                target = candidates[0] if candidates else None
                if target and (target.archived_at or target.status == Sprint.Status.COMPLETED):
                    conflict("спринт EVA завершён или находится в архиве")
                    continue
        values = {key: getattr(item.task, key) for key in ("title", "competency", "description", "external_url")}
        values.update({key: getattr(item.task, key) for key in parsed.relationship_columns})
        if parsed.has_quota_column:
            values["quota"] = item.task.quota
        if parsed.has_sprints_column:
            values["eva_sprints"] = item.eva_sprints
        if parsed.has_status_column:
            values["eva_status"] = item.eva_status
            if item.eva_status:
                values["eva_readiness_stale"] = False
        # Blank EVA is not an instruction to discard locally collected votes awaiting export.
        if item.estimate == 0:
            # Reset the current result, not VotingRound/Vote history. EVA zero means unset.
            values.update(imported_estimate=None, estimate_sum=None, estimate_count=None,
                          status=Task.Status.UNESTIMATED)
        elif item.estimate is not None:
            values.update(imported_estimate=item.estimate, status=Task.Status.ESTIMATED)
        elif task is None or task.estimate is None:
            values.update(imported_estimate=None, status=Task.Status.UNESTIMATED)
        completed = task.completed_at if task else None
        if parsed.has_status_column and item.eva_status:
            completed = (completed or timezone.now()) if item.skip_reason == "closed" else None
            values["completed_at"] = completed
        blocked = "closed" if completed else ""
        if not parsed.has_sprints_column and task and task.eva_block_reason == "eva_assigned":
            blocked = "eva_assigned"
        values["eva_block_reason"] = blocked
        created = task is None
        changed = created or any(getattr(task, key) != val for key, val in values.items())
        if created:
            task = Task.objects.create(project=project, number=item.task.number, **values)
        elif changed:
            for key, val in values.items():
                setattr(task, key, val)
            task.save(update_fields=(*values, "updated_at"))
        membership_changed = False
        if parsed.has_sprints_column:
            if name and target is None:
                target = Sprint.objects.create(project=project, name=name)
                result.counts["sprints_created"] += 1
            for link in current:
                if target and link.sprint_id == target.pk:
                    continue
                link.status = SprintTask.Status.TRANSFERRED if target else SprintTask.Status.REMOVED
                link.transferred_to = target
                link.transferred_at = timezone.now()
                link.save(update_fields=("status", "transferred_to", "transferred_at"))
                membership_changed = True
            if target:
                link, new_link = SprintTask.objects.get_or_create(sprint=target, task=task, defaults={
                    "position": (target.sprint_tasks.aggregate(value=Max("position"))["value"] or 0) + 1,
                })
                if not new_link and link.status != SprintTask.Status.PLANNED:
                    link.status, link.transferred_to, link.transferred_at = SprintTask.Status.PLANNED, None, None
                    link.save(update_fields=("status", "transferred_to", "transferred_at"))
                    membership_changed = True
                membership_changed |= new_link
        result.counts["created" if created else "updated" if changed or membership_changed else "unchanged"] += 1
        if membership_changed:
            result.counts["assignments"] += 1
        if task.estimate is None and not task.completed_at and not task.sprint_items.filter(status=SprintTask.Status.PLANNED).exists():
            result.tasks.append(task)
    # Resolve only after all rows have been saved: export order is irrelevant.
    from .eva_readiness import EvaReadiness
    readiness = EvaReadiness.for_project(project)
    ready_tasks = []
    for task in result.tasks:
        decision = readiness.check(task)
        if decision.allowed:
            ready_tasks.append(task)
        else:
            result.counts["waiting_analysis"] += 1
            result.issues.append(f"{task.number}: сохранена в бэклоге, в очередь оценки не добавлена. {decision.message}")
    result.tasks = ready_tasks
    return result
