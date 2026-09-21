"""Import an EVA board snapshot for planning without manufacturing votes."""

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .models import ESTIMATION_VALUES, Project, SprintTask, Task, VotingRound
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
    estimate: int | None
    eva_status: str
    eva_sprints: str
    skip_reason: str = ""


@dataclass
class PlanningImport:
    rows: list[PlanningTask] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    has_sprints_column: bool = False
    issues: list[str] = field(default_factory=list)


def parse_sprint_file(upload):
    rows = read_task_file_rows(upload)
    columns = _column_mapping(rows[0])
    if "eva_status" not in columns:
        raise ValidationError(
            "Не найдена колонка «Статус.Имя статуса». Выгрузите из EVA все поля. "
            "Колонка «Статус» не заменяет статус задачи."
        )
    result = PlanningImport(has_sprints_column="eva_sprints" in columns)
    seen, identifiers, errors = {}, {}, []
    for line, row in enumerate(rows[1:], 2):
        if not any(_text(cell) for cell in row):
            continue
        result.counts["total"] += 1

        def value(key):
            i = columns.get(key)
            return _text(row[i]) if i is not None and i < len(row) else ""

        if not COMPETENCIES.get(_normalise(value("competency"))):
            result.counts["other_type"] += 1
            continue
        status, sprints = value("eva_status"), value("eva_sprints")
        if not status or len(status) > 200 or len(sprints) > 5000:
            errors.append(f"Строка {line}: проверьте статус задачи и значение спринта EVA.")
            continue
        if _normalise(sprints) in EMPTY_SPRINTS:
            sprints = ""
        reason = ""
        if _normalise(status) in CLOSED_STATUSES or _normalise(value("eva_status_type")) in CLOSED_STATUSES:
            reason = "closed"
        elif sprints:
            reason = "eva_assigned"
        estimate = None
        if not reason:
            raw = value("estimate")
            if not raw:
                reason = "unestimated"
            else:
                try:
                    numeric = Decimal(raw.replace(",", "."))
                except InvalidOperation:
                    numeric = None
                if numeric is None or not numeric.is_finite() or numeric not in ESTIMATION_VALUES:
                    reason = "invalid_estimate"
                    result.issues.append(
                        f"Строка {line}, {value('number')}: оценка «{raw[:40]}» пропущена; "
                        "нужно число из шкалы 1, 2, 4, 8, 12, 20, 32, 52."
                    )
                elif numeric == 0:
                    reason = "unestimated"
                else:
                    estimate = int(numeric)
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
    return (
        project.tasks.filter(
            status=Task.Status.ESTIMATED, completed_at__isnull=True, eva_block_reason="",
        )
        .filter(Q(imported_estimate__isnull=False) | Q(estimate_sum__isnull=False, estimate_count__gt=0))
        .exclude(sprint_items__status=SprintTask.Status.PLANNED)
        .distinct()
    )


@dataclass
class SavedPlanningImport:
    counts: Counter = field(default_factory=Counter)
    issues: list[str] = field(default_factory=list)


@transaction.atomic
def save_sprint_import(project, parsed):
    # Share this project lock with add/transfer. SQLite serialises writers;
    # DBs with row locks also serialise competing planning requests here.
    Project.objects.select_for_update().get(pk=project.pk)
    result = SavedPlanningImport()
    existing = {
        task.number: task for task in project.tasks.select_for_update().filter(
            number__in=[item.task.number for item in parsed.rows]
        )
    }
    for item in parsed.rows:
        task = existing.get(item.task.number)
        if task and task.eva_identifier and task.eva_identifier != item.task.external_url.split("?popup=", 1)[1]:
            result.counts["conflicts"] += 1
            result.issues.append(f"{task.number}: отличается идентификатор EVA.")
            continue
        if project.tasks.filter(external_url=item.task.external_url).exclude(number=item.task.number).exists():
            result.counts["conflicts"] += 1
            result.issues.append(f"{item.task.number}: идентификатор EVA уже связан с другим кодом.")
            continue
        if task and task.eva_sprints and not parsed.has_sprints_column:
            # A missing column is not evidence that an existing EVA assignment ended.
            result.counts["eva_assigned"] += 1
            continue
        # Update availability even for rows skipped by a subsequent board export.
        # Never create closed, assigned, or unestimated tasks through this mode.
        if item.skip_reason:
            if task:
                blocked = item.skip_reason != "unestimated" or task.imported_estimate is not None or task.estimate is None
                task.eva_status, task.eva_sprints = item.eva_status, item.eva_sprints
                task.eva_block_reason = item.skip_reason if blocked else ""
                task.save(update_fields=("eva_status", "eva_sprints", "eva_block_reason", "updated_at"))
            continue
        if task and (task.completed_at or task.sprint_items.filter(status=SprintTask.Status.PLANNED).exists()):
            # A fresh snapshot can reopen availability without changing the plan's hours.
            task.eva_status, task.eva_sprints = item.eva_status, item.eva_sprints
            task.eva_block_reason = ""
            task.save(update_fields=("eva_status", "eva_sprints", "eva_block_reason", "updated_at"))
            result.counts["planned_or_completed"] += 1
            continue
        if task and task.voting_rounds.filter(status__in=(VotingRound.Status.VOTING, VotingRound.Status.REVEALED)).exists():
            result.counts["voting"] += 1
            continue
        if task and task.imported_estimate is None and task.estimate is not None and task.estimate != item.estimate:
            result.counts["conflicts"] += 1
            result.issues.append(f"{task.number}: в score-it {task.estimate} ч, в EVA {item.estimate} ч; сохранён результат голосования.")
            continue
        values = {
            key: getattr(item.task, key)
            for key in ("title", "competency", "description", "external_url")
        }
        values.update(eva_status=item.eva_status, eva_sprints=item.eva_sprints,
                      eva_block_reason="", status=Task.Status.ESTIMATED)
        if task is None or task.imported_estimate is not None or task.estimate is None:
            values["imported_estimate"] = item.estimate
        if task is None:
            Task.objects.create(project=project, number=item.task.number, **values)
            result.counts["created"] += 1
        else:
            changed = [key for key, value in values.items() if getattr(task, key) != value]
            if changed:
                for key in changed:
                    setattr(task, key, values[key])
                task.save(update_fields=(*changed, "updated_at"))
                result.counts["updated"] += 1
            else:
                result.counts["unchanged"] += 1
    return result
